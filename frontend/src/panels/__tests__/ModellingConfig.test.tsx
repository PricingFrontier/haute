import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { act, render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react"
import ModellingConfig from "../ModellingConfig"
import { ApiError } from "../../api/client"
import { trainingIdentityConfig } from "../../utils/modellingExportConfig"
import { trainingLineage } from "../../utils/trainedJobHandles"
import { buildGraph } from "../../utils/buildGraph"
import type { SimpleNode as GraphNodeInput } from "../editors"
import { GraphProvider } from "../GraphContext"
import useGraphStore from "../../stores/useGraphStore"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useNodeResultsStore, { hashConfig } from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useToastStore from "../../stores/useToastStore"
import type { ModellingPane } from "../../stores/useUIStore"
import useUIStore from "../../stores/useUIStore"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import type { SimpleNode, SimpleEdge } from "../editors"
import type { MlflowDestinationEntry, MlflowDestinationKey } from "../../api/types"
import { makeExecutionMetricsFixture } from "../../testSupport/executionMetricsFixture"
import { makeTrainResult as makeCanonicalTrainResult } from "../../test-utils/factories"

// ── Mocks ────────────────────────────────────────────────────────

const mockTrainModel = vi.fn()
const mockCancelTrain = vi.fn()
const mockEstimateTrainingRam = vi.fn()
const mockGetExperiments = vi.fn()
const mockGetMlflowDestinations = vi.fn()
const mockLogToMlflow = vi.fn()
const mockSaveTrainedModel = vi.fn()
const mockResolveModelSaveDestination = vi.fn()
const mockGetTrainStatus = vi.fn()
let defaultPane: ModellingPane = "target"

vi.mock("../../api/client", () => ({
  trainModel: (...args: unknown[]) => mockTrainModel(...args),
  cancelTrain: (...args: unknown[]) => mockCancelTrain(...args),
  estimateTrainingRam: (...args: unknown[]) => mockEstimateTrainingRam(...args),
  getExperiments: (...args: unknown[]) => mockGetExperiments(...args),
  // Reached only if a test leaves the inventory pending — the settings store
  // fetches it for the destination selector the Export pane mounts.
  getMlflowDestinations: (...args: unknown[]) => mockGetMlflowDestinations(...args),
  logToMlflow: (...args: unknown[]) => mockLogToMlflow(...args),
  saveTrainedModel: (...args: unknown[]) => mockSaveTrainedModel(...args),
  resolveModelSaveDestination: (...args: unknown[]) => mockResolveModelSaveDestination(...args),
  // The Export pane reads the job's export receipts, and a reload restores a
  // remembered result through the same status endpoint.
  getTrainStatus: (...args: unknown[]) => mockGetTrainStatus(...args),
  // The Export pane's path picker browses project files when no path is set.
  listFiles: vi.fn(() => Promise.resolve({ items: [] })),
  // GLMTargetConfig narrows errors with `instanceof ApiError`, so the mock
  // must export a real class or the instanceof check throws.
  ApiError: class ApiError extends Error {},
}))

vi.mock("../../api/dispersion", () => ({
  runDispersionEstimate: vi.fn(() => new Promise(() => {})),
}))

vi.mock("../../utils/buildGraph", () => ({
  buildGraph: vi.fn(() => ({ nodes: [], edges: [], preamble: "" })),
}))

// Mock child components that are already well-tested
vi.mock("../modelling/TrainingProgress", () => ({
  TrainingProgress: () => <div data-testid="training-progress" />,
}))

// ── Helpers ──────────────────────────────────────────────────────

const defaultColumns = [
  { name: "loss_ratio", dtype: "Float64" },
  { name: "age", dtype: "Int64" },
  { name: "region", dtype: "String" },
  { name: "exposure", dtype: "Float64" },
]

type ConfigOverrides = Partial<Parameters<typeof ModellingConfig>[0]> & {
  allNodes?: SimpleNode[]
  edges?: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
}

function defaultProps(overrides: ConfigOverrides = {}) {
  // Strip graph-context keys — they flow via `<GraphProvider>` in tests, not props.
  const { allNodes, edges, submodels, preamble, config, ...rest } = overrides
  void allNodes; void edges; void submodels; void preamble
  const evaluation = {
    schema_version: 1,
    strategy: "random",
    seed: 42,
    test: { size: 0.2 },
    validation: { method: "single", size: 0.2 },
  }
  const defaultConfig = {
    _nodeId: "node_1",
    target: "loss_ratio",
    task: "regression",
    algorithm: "catboost",
    loss_function: "RMSE",
    evaluation,
  }
  return {
    // An explicit test config stays exact (for gateway/invalid-state tests)
    // while still receiving the canonical evaluation default when omitted.
    config: config === undefined
      ? defaultConfig
      : { ...config, evaluation: config.evaluation ?? evaluation },
    onUpdate: vi.fn(),
    upstreamColumns: defaultColumns,
    activePane: defaultPane,
    ...rest,
  }
}

/**
 * `buildGraph` is mocked to an empty graph for this file. Lineage tests need the
 * payload to follow the rendered graph, so they install this pass-through.
 */
function payloadFor(
  allNodes: GraphNodeInput[],
  submodels?: Record<string, unknown>,
): ReturnType<typeof buildGraph> {
  return {
    nodes: allNodes.map((node) => ({
      id: node.id,
      type: node.data.nodeType,
      data: node.data,
      position: { x: 0, y: 0 },
    })),
    edges: [],
    submodels,
    preamble: undefined,
  } as unknown as ReturnType<typeof buildGraph>
}

function withPassThroughGraph() {
  vi.mocked(buildGraph).mockImplementation((allNodes, _edges, submodels) => payloadFor(allNodes, submodels))
}

function renderConfig(overrides: ConfigOverrides = {}) {
  const { allNodes = [], edges = [], submodels, preamble } = overrides
  const props = defaultProps(overrides)
  const result = render(
    <GraphProvider allNodes={allNodes} edges={edges} submodels={submodels} preamble={preamble}>
      <ModellingConfig {...props} />
    </GraphProvider>,
  )
  return { ...result, props }
}

function makeTrainResult(overrides: Partial<TrainResult> = {}): TrainResult {
  return makeCanonicalTrainResult({
    final_test_metrics: { gini: 0.45, rmse: 0.12 },
    feature_importance: [
      { feature: "age", importance: 0.6 },
      { feature: "region", importance: 0.4 },
    ],
    model_path: "/models/catboost_model.cbm",
    ...overrides,
  })
}

// ── MLflow inventory helpers ─────────────────────────────────────

type MlflowSlice = ReturnType<typeof useSettingsStore.getState>["mlflow"]

function mlflowEntry(
  key: MlflowDestinationKey,
  over: Partial<MlflowDestinationEntry> = {},
): MlflowDestinationEntry {
  return {
    key,
    configured: true,
    destination: "",
    config_source: "env",
    detail: "",
    probed: false,
    ok: false,
    category: "",
    ...over,
  }
}

const MLFLOW_DATABRICKS = mlflowEntry("databricks", {
  destination: "databricks://team",
  probed: true,
  ok: true,
})
const MLFLOW_SERVER = mlflowEntry("server", {
  destination: "http://mlflow.example:5000",
  config_source: "toml",
  probed: true,
  ok: true,
})
const MLFLOW_LOCAL = mlflowEntry("local", {
  destination: "C:/proj/mlruns",
  config_source: "default",
})

/** Default inventory: local only, so nothing probes and auto is local. */
function setMlflowInventory(over: Partial<MlflowSlice> = {}): void {
  useSettingsStore.setState({
    mlflow: {
      status: "ready",
      installed: true,
      importable: true,
      destinations: [MLFLOW_LOCAL],
      detail: "",
      ...over,
    },
  })
}

// ── Setup / teardown ─────────────────────────────────────────────

beforeEach(() => {
  defaultPane = "target"
  useNodeResultsStore.setState({
    trainJobs: {},
    trainResults: {},
  })
  mockGetTrainStatus.mockReset().mockReturnValue(new Promise(() => {}))
  vi.mocked(buildGraph).mockImplementation(() => ({ nodes: [], edges: [], preamble: "" }) as unknown as ReturnType<typeof buildGraph>)
  useGraphStore.setState(useGraphStore.getInitialState())
  useDocumentStatusStore.setState(useDocumentStatusStore.getInitialState())
  try {
    localStorage.clear()
  } catch {
    // Storage may be unavailable in the test environment.
  }
  mockGetMlflowDestinations.mockReset().mockResolvedValue({
    mlflow_installed: true,
    mlflow_importable: true,
    destinations: [MLFLOW_LOCAL],
    detail: "",
  })
  setMlflowInventory()
  useSettingsStore.setState({ openSections: {} })
  mockTrainModel.mockReset()
  mockCancelTrain.mockReset()
  mockLogToMlflow.mockReset()
  mockSaveTrainedModel.mockReset()
  mockResolveModelSaveDestination.mockReset().mockReturnValue(new Promise(() => {}))
  vi.stubGlobal("confirm", vi.fn(() => true))
  // Return a never-resolving promise by default so the useEffect doesn't cause
  // act() warnings from resolved promises after unmount.
  mockEstimateTrainingRam.mockReset().mockReturnValue(new Promise(() => {}))
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

// ═════════════════════════════════════════════════════════════════
// Config rendering
// ═════════════════════════════════════════════════════════════════

describe("Training configuration readiness", () => {
  afterEach(cleanup)
  it("reports an invalid local draft as a Parameters pane issue", () => {
    const onPaneIssuesChange = vi.fn()
    renderConfig({ activePane: "params", onPaneIssuesChange })
    expect(onPaneIssuesChange).toHaveBeenLastCalledWith("node_1", [])
    fireEvent.change(screen.getByLabelText("CatBoost hyperparameters JSON"), { target: { value: "{" } })
    expect(onPaneIssuesChange).toHaveBeenLastCalledWith("node_1", ["params"])
    fireEvent.change(screen.getByLabelText("CatBoost hyperparameters JSON"), { target: { value: "{}" } })
    expect(onPaneIssuesChange).toHaveBeenLastCalledWith("node_1", [])
  })
  it("reports saved-config issues against the pane that fixes them", () => {
    const onPaneIssuesChange = vi.fn()
    renderConfig({
      activePane: "train",
      onPaneIssuesChange,
      config: { _nodeId: "incomplete", algorithm: "catboost", target: "loss_ratio" },
    })
    expect(onPaneIssuesChange).toHaveBeenLastCalledWith("incomplete", ["target"])
  })
  it.each([
    [{ algorithm: "catboost" }, ["target"]],
    [{ algorithm: "glm", family: "poisson", target: "loss_ratio" }, ["features"]],
    [
      {
        algorithm: "glm",
        family: "poisson",
        target: "loss_ratio",
        terms: { age: { type: "linear" } },
        regularization: "elastic_net",
      },
      ["params"],
    ],
  ])("flags the pane that completes %j", (config, panes) => {
    const onPaneIssuesChange = vi.fn()
    renderConfig({ activePane: "train", onPaneIssuesChange, config: { _nodeId: "setup", ...config } })
    expect(onPaneIssuesChange).toHaveBeenLastCalledWith("setup", panes)
  })
  it("does not let a hidden fixed-parameter draft block a tuned run", () => {
    const onPaneIssuesChange = vi.fn()
    const { rerender, props } = renderConfig({ activePane: "params", onPaneIssuesChange })
    fireEvent.change(screen.getByLabelText("CatBoost hyperparameters JSON"), { target: { value: "{" } })
    expect(onPaneIssuesChange).toHaveBeenLastCalledWith("node_1", ["params"])

    fireEvent.click(screen.getByRole("radio", { name: "Tune parameters" }))
    const tuned = (props.onUpdate as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[0] as Record<string, unknown>
    rerender(
      <GraphProvider allNodes={[]} edges={[]}>
        <ModellingConfig {...props} config={{ ...props.config, ...tuned }} />
      </GraphProvider>,
    )

    expect(screen.queryByText(/Parameters JSON/)).toBeNull()
    expect(onPaneIssuesChange).toHaveBeenLastCalledWith("node_1", [])
  })
  it("shows issues before Train and links to the affected pane", () => {
    renderConfig({ activePane: "train", config: { _nodeId: "readiness", algorithm: "catboost", target: "loss_ratio" } })
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a training loss")
    fireEvent.click(screen.getByRole("button", { name: /Go to Target/ }))
    expect(useUIStore.getState().modellingPanes.readiness).toBe("target")
  })
  it("puts GLM regularization in Parameters", () => {
    renderConfig({ activePane: "params", config: { _nodeId: "glm_params", algorithm: "glm", family: "poisson", target: "loss_ratio", terms: { age: { type: "linear" } } } })
    expect(screen.getByText("Regularization")).toBeInTheDocument()
    expect(screen.queryByText("Target column")).toBeNull()
  })
  it("keeps the missing L1 ratio issue out of Parameters for older Elastic Net configs", () => {
    renderConfig({ activePane: "params", config: { _nodeId: "glm_params", algorithm: "glm", family: "poisson", target: "loss_ratio", terms: { age: { type: "linear" } }, regularization: "elastic_net" } })
    expect(screen.getByRole("slider", { name: "L1 ratio" })).toBeInTheDocument()
    expect(screen.queryByText("Choose an L1 ratio.")).toBeNull()
  })
  it("labels fixed-parameter validation and final fits distinctly", () => {
    renderConfig({ activePane: "train" })
    expect(screen.getByLabelText("Training run summary")).toHaveTextContent("2 total fits: 1 validation fit + 1 final fit")
    expect(screen.getByLabelText("Training run summary")).toHaveTextContent("3 features")
  })
  it("shows only the final fit when validation is disabled", () => {
    renderConfig({ activePane: "train", config: {
      _nodeId: "no_validation", target: "loss_ratio", algorithm: "catboost", loss_function: "RMSE",
      evaluation: { schema_version: 1, strategy: "random", seed: 42, validation: { method: "none" }, test: null },
    } })
    expect(screen.getByLabelText("Training run summary")).toHaveTextContent("1 final fit")
  })
  it("labels parameter-search runs as tuning fits", () => {
    renderConfig({ activePane: "train", config: {
      _nodeId: "tuning", target: "loss_ratio", algorithm: "catboost", loss_function: "RMSE",
      tuning: { trial_count: 3 },
    } })
    expect(screen.getByLabelText("Training run summary")).toHaveTextContent("4 total fits: 3 tuning fits + 1 final fit")
  })
})

describe("ModellingConfig", () => {
  describe("Config rendering", () => {
    it("renders target column dropdown with upstream columns", () => {
      renderConfig()
      const targetPicker = screen.getByRole("button", { name: "Target column" })
      expect(targetPicker).toHaveTextContent("loss_ratio")
      fireEvent.click(targetPicker)
      expect(screen.getByRole("listbox")).toHaveTextContent("loss_ratioFloat64")
      expect(screen.getByRole("listbox")).toHaveTextContent("ageInt64")
    })

    it("renders weight column dropdown with 'None' default", () => {
      renderConfig()
      expect(screen.getByRole("button", { name: "Weight column" })).toHaveTextContent("None")
    })

    it("does not render a separate task selector", () => {
      renderConfig()
      expect(screen.queryByText("Task")).toBeNull()
      expect(screen.queryByRole("button", { name: "regression" })).toBeNull()
      expect(screen.queryByRole("button", { name: "classification" })).toBeNull()
    })

    it("feature count shows correct number (excludes target and weight)", () => {
      renderConfig({ activePane: "features" })
      // 4 columns total. Target=loss_ratio excluded, weight="" so not excluded.
      // Feature columns: age, region, exposure = 3 of 4
      expect(
        screen.getAllByRole("checkbox", { name: /^Include / }),
      ).toHaveLength(3)
      expect(screen.getByText("3 included · 0 excluded")).toBeInTheDocument()
    })

    it("feature count adjusts when weight is set", () => {
      renderConfig({
        activePane: "features",
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", weight: "exposure" },
      })
      // Target=loss_ratio, weight=exposure both excluded. Features: age, region = 2 of 4
      expect(
        screen.getAllByRole("checkbox", { name: /^Include / }),
      ).toHaveLength(2)
      expect(screen.getByText("2 included · 0 excluded")).toBeInTheDocument()
    })

    it("exclude column toggles work", () => {
      vi.spyOn(window, "confirm").mockReturnValue(true)
      const { props } = renderConfig({ activePane: "features" })
      fireEvent.click(
        within(screen.getByRole("group", { name: "age feature" })).getByRole(
          "checkbox",
          { name: "Include age" },
        ),
      )
      expect(props.onUpdate).toHaveBeenCalledWith({ exclude: ["age"] })
    })

    it("excluded column re-includes on second click", () => {
      const { props } = renderConfig({
        activePane: "features",
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", exclude: ["age"] },
      })
      fireEvent.click(
        within(screen.getByRole("group", { name: "age feature" })).getByRole(
          "checkbox",
          { name: "Include age" },
        ),
      )
      // Should remove "age" from exclusion list
      expect(props.onUpdate).toHaveBeenCalledWith({ exclude: [] })
    })

    it("shows algorithm picker when algorithm is not set", () => {
      renderConfig({ config: { _nodeId: "node_1", target: "loss_ratio", task: "regression" } })
      expect(screen.getByText("Select algorithm")).toBeTruthy()
      expect(screen.getByText("CatBoost")).toBeTruthy()
    })

    it("reports a non-string algorithm as unsupported instead of crashing", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: 42,
        },
      })

      expect(screen.getByRole("alert")).toHaveTextContent(
        "Unsupported modelling algorithm: 42.",
      )
    })

    it("clicking CatBoost in picker sets algorithm and shows full config", () => {
      const { props } = renderConfig({ config: { _nodeId: "node_1", target: "loss_ratio", task: "regression" } })
      fireEvent.click(screen.getByText("CatBoost"))
      expect(props.onUpdate).toHaveBeenCalledWith({
        algorithm: "catboost",
        params: { iterations: 1000, learning_rate: 0.05, depth: 6, l2_leaf_reg: 3, early_stopping_rounds: 50 },
        evaluation: expect.objectContaining({
          schema_version: 1,
          strategy: "random",
        }),
      })
    })

    it("shows every supported loss in one picker", () => {
      renderConfig()
      const losses = within(screen.getByRole("group", { name: "Loss functions" }))
      for (const loss of ["RMSE", "MAE", "Poisson", "Tweedie", "Logloss", "CrossEntropy"]) {
        expect(losses.getByRole("button", { name: loss })).toBeTruthy()
      }
    })

    it("Tweedie variance power slider only visible when loss_function=Tweedie", () => {
      // Without Tweedie: no slider
      const { unmount } = render(
        <GraphProvider allNodes={[]} edges={[]}>
          <ModellingConfig {...defaultProps()} />
        </GraphProvider>,
      )
      expect(screen.queryByLabelText("Variance power")).toBeNull()
      unmount()

      // With Tweedie: slider visible
      render(
        <GraphProvider allNodes={[]} edges={[]}>
          <ModellingConfig
            {...defaultProps({
              config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", loss_function: "Tweedie" },
            })}
          />
        </GraphProvider>,
      )
      expect(screen.getByText(/Variance power/)).toBeTruthy()
      expect(screen.getByRole("spinbutton", { name: "Variance power" })).toHaveValue(null)
      expect(screen.getByRole("alert")).toHaveTextContent("Tweedie variance power")
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Hyperparameter JSON editor
  // ═════════════════════════════════════════════════════════════════

  describe("Hyperparameter JSON editor", () => {
    beforeEach(() => { defaultPane = "params" })
    it("renders one visible JSON editor without individual parameter fields", () => {
      renderConfig()
      expect(screen.getByLabelText("CatBoost hyperparameters JSON")).toBeVisible()
      expect(document.querySelectorAll("textarea")).toHaveLength(1)
      expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument()
    })

    it("textarea shows an empty object when config.params is empty", () => {
      renderConfig()
      const editor = screen.getByLabelText(
        "CatBoost hyperparameters JSON",
      ) as HTMLTextAreaElement
      expect(editor.value).toBe("{}")
    })

    it("textarea shows custom params from config", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", params: { iterations: 500, depth: 8 } },
      })
      const textarea = screen.getByLabelText("CatBoost hyperparameters JSON") as HTMLTextAreaElement
      const parsed = JSON.parse(textarea.value)
      expect(parsed).toEqual({ iterations: 500, depth: 8 })
    })

    it("autosaves arbitrary algorithm parameters", () => {
      const { props } = renderConfig()
      const textarea = screen.getByLabelText("CatBoost hyperparameters JSON")
      fireEvent.change(textarea, {
        target: {
          value: '{"grow_policy":"Lossguide","max_leaves":64,"custom":{"enabled":true}}',
        },
      })
      expect(props.onUpdate).toHaveBeenCalledWith("params", {
        grow_policy: "Lossguide",
        max_leaves: 64,
        custom: { enabled: true },
      })
    })

    it("invalid JSON shows an immediate inline error without a config commit", () => {
      const { props } = renderConfig()
      const textarea = screen.getByLabelText("CatBoost hyperparameters JSON")
      fireEvent.change(textarea, { target: { value: "{bad json" } })
      expect(screen.getByRole("alert")).toHaveTextContent("Parameters JSON")
      expect(props.onUpdate).not.toHaveBeenCalledWith("params", expect.anything())
    })

    it("strips task_type from JSON display when GPU is enabled", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", params: { iterations: 500, task_type: "GPU" } },
      })
      const textarea = screen.getByLabelText("CatBoost hyperparameters JSON") as HTMLTextAreaElement
      const parsed = JSON.parse(textarea.value)
      expect(parsed).not.toHaveProperty("task_type")
      expect(parsed).toEqual({ iterations: 500 })
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Split/Eval section
  // ═════════════════════════════════════════════════════════════════

  describe("Split/Eval section", () => {
    beforeEach(() => { defaultPane = "split" })
    it("renders the three data-structure choices", () => {
      renderConfig()
      expect(screen.getByRole("button", { name: "Random split" })).toBeTruthy()
      expect(screen.getByRole("button", { name: "Time-based split" })).toBeTruthy()
      expect(screen.getByRole("button", { name: "Group split" })).toBeTruthy()
    })

    it("random evaluation shows validation and final-test inputs with the seed kept internal", () => {
      renderConfig()
      expect(screen.getByLabelText("Validation set (%)")).toHaveValue(20)
      expect(screen.getByLabelText("Test set (%)")).toHaveValue(20)
      expect(screen.queryByLabelText("Evaluation seed")).not.toBeInTheDocument()
    })

    it("changing the data structure commits canonical temporal evaluation", () => {
      const { props } = renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "Time-based split" }))
      expect(props.onUpdate).toHaveBeenCalledWith(
        "evaluation",
        expect.objectContaining({ strategy: "temporal" }),
      )
    })

    it("restores the required refit when leaving holdout validation", () => {
      const { props } = renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "catboost",
          loss_function: "RMSE",
          refit_on_development: false,
        },
      })
      fireEvent.click(screen.getByRole("button", { name: "Cross-validation" }))
      expect(props.onUpdate).toHaveBeenCalledWith({
        evaluation: expect.objectContaining({
          validation: expect.objectContaining({ method: "cross_validation" }),
        }),
        refit_on_development: true,
      })
    })

    it("temporal evaluation shows date and boundary controls", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "catboost",
          evaluation: {
            schema_version: 1,
            strategy: "temporal",
            date_column: "",
            validation: { method: "single", start: "" },
            test: { start: "" },
          },
        },
      })
      expect(screen.getByText("Date column")).toBeTruthy()
      expect(screen.getByText("Validation starts")).toBeTruthy()
      expect(screen.getByText("Test starts")).toBeTruthy()
    })

    it("group evaluation shows the entity column", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "catboost",
          evaluation: {
            schema_version: 1,
            strategy: "group",
            group_column: "",
            seed: 42,
            validation: { method: "single", size: 0.2 },
            test: { size: 0.2 },
          },
        },
      })
      expect(screen.getByText("Group column")).toBeTruthy()
    })

    it("shows all metrics and disables classification metrics for a regression loss", () => {
      renderConfig({ activePane: "target" })
      const metricButtons = within(screen.getByRole("group", { name: "Metrics" }))
      expect(metricButtons.getByRole("button", { name: "Gini" })).toBeEnabled()
      expect(metricButtons.getByRole("button", { name: "R²" })).toBeEnabled()
      expect(metricButtons.getByRole("button", { name: "AUC" })).toBeDisabled()
      expect(metricButtons.getByRole("button", { name: "Logloss" })).toBeDisabled()
    })

    it("clicking a metric button toggles it", () => {
      const { props } = renderConfig({
        activePane: "target",
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", loss_function: "RMSE", metrics: ["gini", "rmse"] },
      })
      // Click "MSE" metric to add it (only appears once — not a loss function)
      fireEvent.click(screen.getByRole("button", { name: "MSE" }))
      expect(props.onUpdate).toHaveBeenCalledWith("metrics", ["gini", "rmse", "mse"])
    })

    it("clicking a selected metric removes it", () => {
      const { props } = renderConfig({
        activePane: "target",
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", loss_function: "RMSE", metrics: ["gini", "rmse"] },
      })
      // Click "Gini" to remove it (only appears once — not a loss function)
      fireEvent.click(screen.getByRole("button", { name: "Gini" }))
      expect(props.onUpdate).toHaveBeenCalledWith("metrics", ["rmse"])
    })

    it("shows all metrics and disables regression metrics for a classification loss", () => {
      renderConfig({
        activePane: "target",
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "classification",
          algorithm: "catboost",
          loss_function: "Logloss",
          metrics: ["auc", "logloss"],
        },
      })
      const metricButtons = within(screen.getByRole("group", { name: "Metrics" }))
      expect(metricButtons.getByRole("button", { name: "AUC" })).toBeEnabled()
      expect(metricButtons.getByRole("button", { name: "Logloss" })).toBeEnabled()
      expect(metricButtons.getByRole("button", { name: "Gini" })).toBeDisabled()
      expect(metricButtons.getByRole("button", { name: "RMSE" })).toBeDisabled()
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Training actions
  // ═════════════════════════════════════════════════════════════════

  describe("Training actions", () => {
    beforeEach(() => { defaultPane = "train" })
    it("train button calls trainModel API with graph and node_id", async () => {
      mockTrainModel.mockResolvedValue({ status: "started", job_id: "job_1" })
      renderConfig()
      fireEvent.click(screen.getByRole("button", { name: /Train Model/ }))
      await waitFor(() => expect(mockTrainModel).toHaveBeenCalledTimes(1))
      const callArgs = mockTrainModel.mock.calls[0][0]
      expect(callArgs).toEqual(
        expect.objectContaining({
          graph: expect.any(Object),
          node_id: "node_1",
        }),
      )
    })

    it("records the lineage of the graph it submitted, not one edited while the request was pending", async () => {
      let respond: (value: unknown) => void = () => {}
      mockTrainModel.mockReturnValue(new Promise((resolve) => { respond = resolve }))
      withPassThroughGraph()
      const upstream = { id: "source", data: { label: "source", description: "", nodeType: "polars", code: "df" } }
      const { rerender, props } = renderConfig({ allNodes: [upstream] })
      fireEvent.click(screen.getByRole("button", { name: /Train Model/ }))
      await waitFor(() => expect(mockTrainModel).toHaveBeenCalledTimes(1))
      const submitted = mockTrainModel.mock.calls[0][0].graph

      const edited = [{ ...upstream, data: { ...upstream.data, code: "df.head(10)" } }]
      rerender(
        <GraphProvider allNodes={edited} edges={[]}>
          <ModellingConfig {...props} />
        </GraphProvider>,
      )
      await act(async () => {
        respond({ status: "started", job_id: "job_pending" })
      })

      const job = useNodeResultsStore.getState().trainJobs.node_1
      expect(job?.jobId).toBe("job_pending")
      expect(job?.lineage).toBe(trainingLineage(submitted))
      expect(job?.lineage).not.toBe(trainingLineage(payloadFor(edited)))
    })

    it("keeps validation hidden and Train enabled before the first press", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "", task: "regression", algorithm: "catboost" },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toBeEnabled()
      expect(screen.getByRole("alert")).toBeInTheDocument()
    })

    it("shows all missing items beneath Train only after the press and sends no request", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "",
          task: "regression",
          algorithm: "glm",
        },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })

      fireEvent.click(trainBtn)

      const banner = screen.getByRole("alert")
      expect(trainBtn.nextElementSibling).toBe(banner)
      expect(banner).toHaveTextContent("Select a target column.")
      expect(banner).toHaveTextContent("Choose a GLM distribution family")
      expect(banner).toHaveTextContent("Add a term to at least one feature")
      expect(mockTrainModel).not.toHaveBeenCalled()
    })

    it("resets the reveal after the configuration becomes valid", () => {
      const incompleteConfig = {
        _nodeId: "node_1",
        target: "loss_ratio",
        task: "regression",
        algorithm: "catboost",
      }
      const completeConfig = {
        ...incompleteConfig,
        loss_function: "RMSE",
      }
      const renderWithConfig = (config: Record<string, unknown>) => (
        <GraphProvider allNodes={[]} edges={[]}>
          <ModellingConfig {...defaultProps({ config })} />
        </GraphProvider>
      )
      const view = render(renderWithConfig(incompleteConfig))

      fireEvent.click(screen.getByRole("button", { name: /Train Model/ }))
      expect(screen.getByRole("alert")).toHaveTextContent("Choose a training loss")

      view.rerender(renderWithConfig(completeConfig))
      expect(screen.queryByRole("alert")).not.toBeInTheDocument()

      view.rerender(renderWithConfig(incompleteConfig))
      expect(screen.getByRole("alert")).toHaveTextContent("Choose a training loss")
    })

    it("surfaces a missing loss function only after Train is pressed (catboost)", () => {
      // The backend rejects an unset training objective (it would otherwise
      // silently train under CatBoost's RMSE default) — the UI must not
      // submit one.
      renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost" },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toBeEnabled()
      expect(screen.getByText(/Choose a training loss/)).toBeInTheDocument()
      fireEvent.click(trainBtn)
      expect(screen.getByRole("alert")).toHaveTextContent("Choose a training loss")
      expect(mockTrainModel).not.toHaveBeenCalled()
    })

    it("surfaces a missing family only after Train is pressed (glm)", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "glm" },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toBeEnabled()
      expect(screen.getByText(/Choose a GLM distribution family/)).toBeInTheDocument()
      fireEvent.click(trainBtn)
      expect(screen.getByRole("alert")).toHaveTextContent("Choose a GLM distribution family")
    })

    it("surfaces an empty term set only after Train is pressed (glm)", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "glm",
          family: "poisson",
        },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toBeEnabled()
      expect(screen.getByText(/Add a term to at least one feature/)).toBeInTheDocument()
      fireEvent.click(trainBtn)
      expect(screen.getByRole("alert")).toHaveTextContent("Add a term to at least one feature")
    })

    it("surfaces missing Tweedie variance power only after Train is pressed (glm)", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "glm",
          family: "tweedie",
          terms: { age: { type: "linear" } },
        },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toBeEnabled()
      expect(screen.getByText(/Set the Tweedie variance power/)).toBeInTheDocument()
      fireEvent.click(trainBtn)
      expect(screen.getByRole("alert")).toHaveTextContent("Set the Tweedie variance power")
    })

    it("surfaces missing Neg. Binomial theta only after Train is pressed (glm)", () => {
      // RustyStats does not estimate theta and refuses to fit without it,
      // so the UI must not submit an unset value.
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "glm",
          family: "negbinomial",
          terms: { age: { type: "linear" } },
        },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toBeEnabled()
      expect(screen.getByText(/Set the Negative Binomial dispersion/)).toBeInTheDocument()
      fireEvent.click(trainBtn)
      expect(screen.getByRole("alert")).toHaveTextContent("Set the Negative Binomial dispersion")
    })

    it("train button enables on Neg. Binomial once theta is set (glm)", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "glm",
          family: "negbinomial",
          terms: { age: { type: "linear" } },
          theta: 2.5,
        },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toHaveProperty("disabled", false)
    })

    it("surfaces missing elastic-net L1 ratio only after Train is pressed (glm)", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "glm",
          family: "poisson",
          terms: { age: { type: "linear" } },
          regularization: "elastic_net",
        },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toBeEnabled()
      expect(screen.getByText("Choose an L1 ratio.")).toBeInTheDocument()
      fireEvent.click(trainBtn)
      expect(screen.getByRole("alert")).toHaveTextContent("Choose an L1 ratio.")
    })

    it("train button enables once the objective is explicit", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "glm",
          family: "poisson",
          terms: { age: { type: "linear" } },
        },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toBeEnabled()
      expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    })

    it("train button shows 'Training...' when job is active", () => {
      useNodeResultsStore.setState({
        trainJobs: {
          node_1: {
            jobId: "job_1",
            nodeId: "node_1",
            nodeLabel: "Model",
            progress: null,
            error: null,
            configHash: "abc",
            source: "live",
            structuralVersion: 0,
          },
        },
      })
      renderConfig()
      expect(screen.getByRole("button", { name: /Training\.\.\./ })).toBeTruthy()
      expect(screen.getByRole("button", { name: /Training\.\.\./ })).toHaveProperty("disabled", true)
    })

    it("cancels the active preparation job and records its terminal state", async () => {
      useNodeResultsStore.setState({
        trainJobs: {
          node_1: {
            jobId: "job_1",
            nodeId: "node_1",
            nodeLabel: "Model",
            progress: {
              status: "running",
              progress: 0.1,
              message: "Preparing training data...",
              iteration: 0,
              total_iterations: 0,
              train_loss: {},
              elapsed_seconds: 1,
            },
            error: null,
            configHash: "abc",
            source: "live",
            structuralVersion: 0,
          },
        },
      })
      mockCancelTrain.mockResolvedValue({
        status: "cancelled",
        progress: 0.1,
        message: "Cancelled",
        iteration: 0,
        total_iterations: 0,
        train_loss: {},
        elapsed_seconds: 1,
        result: null,
        terminal_reason: "cancelled",
      })

      renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "Cancel training" }))

      await waitFor(() => expect(mockCancelTrain).toHaveBeenCalledWith("job_1"))
      await waitFor(() => {
        const state = useNodeResultsStore.getState()
        expect(state.trainJobs.node_1).toBeUndefined()
        expect(state.trainResults.node_1?.terminalStatus?.status).toBe("cancelled")
        expect(state.trainResults.node_1?.result.error).toBe("Cancelled")
      })
    })

    it("stores error result when trainModel throws", async () => {
      mockTrainModel.mockRejectedValue(new Error("Network fail"))
      renderConfig()
      fireEvent.click(screen.getByRole("button", { name: /Train Model/ }))
      await waitFor(() => {
        const store = useNodeResultsStore.getState()
        const cached = store.trainResults.node_1
        expect(cached).toBeTruthy()
        expect(cached.result.status).toBe("error")
        expect(cached.result.error).toBe("Error: Network fail")
      })
    })

    it("preserves structured execution metrics when trainModel admission fails", async () => {
      const executionMetrics = makeExecutionMetricsFixture({
        profile: "training_prep",
        status: "memory_limited",
        terminal_reason: "memory_limited",
      })
      mockTrainModel.mockRejectedValue(Object.assign(new Error("HTTP 507"), {
        name: "ApiError",
        status: 507,
        detail: JSON.stringify({
          message: "Training rejected by admission control",
          terminal_reason: "memory_limited",
          execution_metrics: executionMetrics,
        }),
        rawDetail: {
          message: "Training rejected by admission control",
          terminal_reason: "memory_limited",
          execution_metrics: executionMetrics,
        },
      }))

      renderConfig()
      fireEvent.click(screen.getByRole("button", { name: /Train Model/ }))

      await waitFor(() => {
        const cached = useNodeResultsStore.getState().trainResults.node_1
        expect(cached?.result.error).toBe("Training rejected by admission control")
        expect(cached?.terminalStatus?.status).toBe("memory_limited")
        expect(cached?.terminalStatus?.terminal_reason).toBe("memory_limited")
        expect(cached?.terminalStatus?.execution_metrics).toBe(executionMetrics)
      })
    })

    it("stores synchronous result when trainModel returns non-started status", async () => {
      const result = makeTrainResult()
      mockTrainModel.mockResolvedValue(result)
      renderConfig()
      fireEvent.click(screen.getByRole("button", { name: /Train Model/ }))
      await waitFor(() => {
        const store = useNodeResultsStore.getState()
        const cached = store.trainResults.node_1
        expect(cached).toBeTruthy()
        expect(cached.result.status).toBe("completed")
      })
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Staleness indicator
  // ═════════════════════════════════════════════════════════════════

  describe("Staleness indicator", () => {
    beforeEach(() => { defaultPane = "train" })
    it("shows staleness warning when config hash changed after training", () => {
      // Put a cached result with a different config hash
      useNodeResultsStore.setState({
        trainResults: {
          node_1: {
            result: makeTrainResult(),
            jobId: "job_1",
            configHash: "old_hash_that_wont_match",
            source: "live",
            structuralVersion: 0,
          },
        },
      })
      renderConfig()
      expect(screen.getByText("Config changed since last training")).toBeTruthy()
      expect(screen.getByRole("button", { name: "Re-train" })).toBeTruthy()
    })

    it("does not show staleness warning when config hash matches", () => {
      const config = {
        _nodeId: "node_1",
        target: "loss_ratio",
        task: "regression",
        algorithm: "catboost",
        evaluation: {
          schema_version: 1,
          strategy: "random",
          seed: 42,
          test: { size: 0.2 },
          validation: { method: "single", size: 0.2 },
        },
      }
      const hash = hashConfig(config)

      useNodeResultsStore.setState({
        trainResults: {
          node_1: {
            result: makeTrainResult(),
            jobId: "job_1",
            configHash: hash,
            source: "live",
            structuralVersion: 0,
          },
        },
      })
      renderConfig({ config })
      expect(screen.queryByText("Config changed since last training")).toBeNull()
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Training results
  // ═════════════════════════════════════════════════════════════════

  describe("Training results", () => {
    beforeEach(() => { defaultPane = "train" })
    it("shows training progress panel when trainJob has progress", () => {
      useNodeResultsStore.setState({
        trainJobs: {
          node_1: {
            jobId: "job_1",
            nodeId: "node_1",
            nodeLabel: "Model",
            progress: {
              status: "running",
              progress: 0.5,
              message: "Training...",
              iteration: 50,
              total_iterations: 100,
              train_loss: { rmse: 0.1 },
              elapsed_seconds: 10,
            },
            error: null,
            configHash: "abc",
            source: "live",
            structuralVersion: 0,
          },
        },
      })
      renderConfig()
      expect(screen.getByTestId("training-progress")).toBeTruthy()
    })

    it("shows error message when trainResult.status === 'error'", () => {
      useNodeResultsStore.setState({
        trainResults: {
          node_1: {
            result: makeTrainResult({ status: "error", error: "OOM: out of memory" }),
            jobId: "job_1",
            configHash: "irrelevant",
            source: "live",
            structuralVersion: 0,
          },
        },
      })
      renderConfig()
      expect(screen.getByText("Training failed")).toBeTruthy()
      expect(screen.getByText("OOM: out of memory")).toBeTruthy()
    })

    it("shows completion badge when trainResult is successful and not training", () => {
      useNodeResultsStore.setState({
        trainResults: {
          node_1: {
            result: makeTrainResult(),
            jobId: "job_1",
            configHash: "irrelevant",
            source: "live",
            structuralVersion: 0,
          },
        },
      })
      renderConfig()
      expect(screen.getByText(/Model trained — results in preview panel below/)).toBeTruthy()
    })

    it("does not show completion badge when training is active", () => {
      useNodeResultsStore.setState({
        trainJobs: {
          node_1: {
            jobId: "job_1",
            nodeId: "node_1",
            nodeLabel: "Model",
            progress: null,
            error: null,
            configHash: "abc",
            source: "live",
            structuralVersion: 0,
          },
        },
        trainResults: {
          node_1: {
            result: makeTrainResult(),
            jobId: "job_1",
            configHash: "abc",
            source: "live",
            structuralVersion: 0,
          },
        },
      })
      renderConfig()
      expect(screen.queryByText(/Model trained — results in preview panel below/)).toBeNull()
    })

    it("does not show completion badge for error results", () => {
      useNodeResultsStore.setState({
        trainResults: {
          node_1: {
            result: makeTrainResult({ status: "error", error: "fail" }),
            jobId: "job_1",
            configHash: "irrelevant",
            source: "live",
            structuralVersion: 0,
          },
        },
      })
      renderConfig()
      expect(screen.queryByText(/Model trained — results in preview panel below/)).toBeNull()
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // RAM estimate
  // ═════════════════════════════════════════════════════════════════

  describe("RAM estimate", () => {
    beforeEach(() => { defaultPane = "train" })
    it("calls estimateTrainingRam on mount", () => {
      renderConfig()
      expect(mockEstimateTrainingRam).toHaveBeenCalledTimes(1)
      const callArgs = mockEstimateTrainingRam.mock.calls[0][0]
      expect(callArgs).toEqual(
        expect.objectContaining({
          graph: expect.any(Object),
          node_id: "node_1",
        }),
      )
    })

    it("shows loading state while estimating", () => {
      // The mock returns a never-resolving promise, so loading persists
      renderConfig()
      expect(screen.getByText("Estimating dataset size...")).toBeTruthy()
    })

    it("shows RAM estimate data when resolved", async () => {
      // bytes_per_row=700 → 700 * 100k * 1.0 / 1024² ≈ 67 MB
      mockEstimateTrainingRam.mockResolvedValue({
        total_rows: 100000,
        safe_row_limit: null,
        estimated_mb: 50,
        training_mb: 67,
        available_mb: 8192,
        bytes_per_row: 700,
        was_downsampled: false,
        warning: null,
        gpu_vram_estimated_mb: null,
        gpu_vram_available_mb: null,
        gpu_warning: null,
      })
      renderConfig()
      await waitFor(() => {
        expect(screen.getByText("Dataset fits in memory")).toBeTruthy()
        expect(screen.getByText("100,000")).toBeTruthy()
        expect(screen.getByText("67 MB")).toBeTruthy()
      })
    })

    it("shows downsample warning when was_downsampled is true", async () => {
      mockEstimateTrainingRam.mockResolvedValue({
        total_rows: 5000000,
        safe_row_limit: 1000000,
        estimated_mb: 2500,
        training_mb: 10000,
        available_mb: 8192,
        bytes_per_row: 500,
        was_downsampled: true,
        warning: null,
        gpu_vram_estimated_mb: null,
        gpu_vram_available_mb: null,
        gpu_warning: null,
      })
      renderConfig()
      await waitFor(() => {
        expect(screen.getByText("Will downsample")).toBeTruthy()
        expect(screen.getByText((1000000).toLocaleString())).toBeTruthy()
      })
    })

    it("shows GPU VRAM info when gpu fields present", async () => {
      mockEstimateTrainingRam.mockResolvedValue({
        total_rows: 100000,
        safe_row_limit: null,
        estimated_mb: 50,
        training_mb: 200,
        available_mb: 8192,
        bytes_per_row: 500,
        was_downsampled: false,
        gpu_vram_estimated_mb: 512,
        gpu_vram_available_mb: 8192,
        warning: null,
        gpu_warning: null,
      })
      renderConfig()
      await waitFor(() => {
        expect(screen.getByText("Est. GPU VRAM")).toBeTruthy()
        expect(screen.getByText("512 MB")).toBeTruthy()
      })
    })

    it("shows GPU warning when estimated VRAM exceeds available", async () => {
      mockEstimateTrainingRam.mockResolvedValue({
        total_rows: 100000,
        safe_row_limit: null,
        estimated_mb: 50,
        training_mb: 200,
        available_mb: 8192,
        bytes_per_row: 500,
        was_downsampled: false,
        gpu_vram_estimated_mb: 12000,
        gpu_vram_available_mb: 8192,
        warning: null,
        gpu_warning: "GPU training needs 12000 MB but GPU has 8192 MB",
      })
      renderConfig()
      await waitFor(() => {
        expect(screen.getByText(/GPU training needs.*but GPU has/)).toBeTruthy()
      })
    })

    it("shows inline warning and toast when RAM estimate fails", async () => {
      mockEstimateTrainingRam.mockRejectedValue(new Error("Network error"))
      renderConfig()
      // Wait for loading to finish and error state to propagate
      await waitFor(() => {
        // The toast store should have received the warning
        const toasts = useToastStore.getState().toasts
        expect(toasts.some((t) => t.text.includes("Training estimate failed"))).toBe(true)
      })
      // Inline warning is shown
      expect(screen.getByText(/Memory estimate unavailable/)).toBeTruthy()
      // Verify toast content
      const toasts = useToastStore.getState().toasts
      const ramToast = toasts.find((t) => t.text.includes("Training estimate failed"))!
      expect(ramToast.type).toBe("warning")
      expect(ramToast.text).toContain("Network error")
    })

    it("does not show inline warning when estimate succeeds", async () => {
      mockEstimateTrainingRam.mockResolvedValue({
        total_rows: 100000,
        safe_row_limit: null,
        estimated_mb: 50,
        training_mb: 200,
        available_mb: 8192,
        bytes_per_row: 700,
        was_downsampled: false,
        warning: null,
        gpu_vram_estimated_mb: null,
        gpu_vram_available_mb: null,
        gpu_warning: null,
      })
      renderConfig()
      await waitFor(() => {
        expect(screen.getByText("Dataset fits in memory")).toBeTruthy()
      })
      expect(screen.queryByText(/Memory estimate unavailable/)).toBeNull()
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Collapsible sections
  // ═════════════════════════════════════════════════════════════════

  // ═════════════════════════════════════════════════════════════════
  // Edge cases
  // ═════════════════════════════════════════════════════════════════

  describe("Edge cases", () => {
    beforeEach(() => { defaultPane = "features" })
    it("renders without upstream columns", () => {
      renderConfig({ upstreamColumns: undefined })
      // Should not crash, feature count section still renders
      expect(screen.getByText(/Features/)).toBeTruthy()
    })

    it("renders with empty columns array", () => {
      renderConfig({ upstreamColumns: [] })
      expect(screen.getByText(/Features/)).toBeTruthy()
    })

    it("GPU toggle enables GPU training", () => {
      const { props } = renderConfig({ activePane: "train" })
      const gpuCheckbox = screen.getByRole("checkbox")
      fireEvent.click(gpuCheckbox)
      expect(props.onUpdate).toHaveBeenCalledWith("params", expect.objectContaining({ task_type: "GPU" }))
    })

    it("GPU unchecked removes task_type from params", () => {
      const { props } = renderConfig({
        activePane: "train",
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", params: { iterations: 500, task_type: "GPU" } },
      })
      const gpuCheckbox = screen.getByRole("checkbox")
      fireEvent.click(gpuCheckbox)
      // Should commit params without task_type
      expect(props.onUpdate).toHaveBeenCalledWith("params", { iterations: 500 })
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Algorithm picker — GLM option
  // ═════════════════════════════════════════════════════════════════

  describe("Algorithm picker", () => {
    it("shows both CatBoost and GLM options when algorithm is not set", () => {
      renderConfig({ config: { _nodeId: "node_1", target: "loss_ratio", task: "regression" } })
      expect(screen.getByText("CatBoost")).toBeTruthy()
      expect(screen.getByText("GLM")).toBeTruthy()
    })

    it("clicking GLM in picker sets algorithm to glm", () => {
      const { props } = renderConfig({ config: { _nodeId: "node_1", target: "loss_ratio", task: "regression" } })
      fireEvent.click(screen.getByText("GLM"))
      expect(props.onUpdate).toHaveBeenCalledWith({
        algorithm: "glm",
        evaluation: expect.objectContaining({
          schema_version: 1,
          strategy: "random",
        }),
      })
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Loss selection derives task and metrics
  // ═════════════════════════════════════════════════════════════════

  describe("Loss-derived task and metrics", () => {
    it("selecting Logloss sets classification task and metrics", () => {
      const { props } = renderConfig()
      fireEvent.click(within(screen.getByRole("group", { name: "Loss functions" })).getByRole("button", { name: "Logloss" }))
      expect(props.onUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ loss_function: "Logloss", task: "classification", metrics: ["auc", "logloss"] }),
      )
    })

    it("selecting RMSE sets regression task and metrics", () => {
      const { props } = renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "classification",
          algorithm: "catboost",
          loss_function: "Logloss",
          metrics: ["auc", "logloss"],
        },
      })
      fireEvent.click(within(screen.getByRole("group", { name: "Loss functions" })).getByRole("button", { name: "RMSE" }))
      expect(props.onUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ loss_function: "RMSE", task: "regression", metrics: ["gini", "rmse"] }),
      )
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Loss function selection (regression)
  // ═════════════════════════════════════════════════════════════════

  describe("Loss function selection", () => {
    it("clicking Poisson sets loss_function and objective-matched metrics", () => {
      const { props } = renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "Poisson" }))
      expect(props.onUpdate).toHaveBeenCalledWith({
        loss_function: "Poisson",
        task: "regression",
        metrics: ["gini", "poisson_deviance"],
      })
    })

    it("clicking Tweedie sets loss_function and objective-matched metrics", () => {
      const { props } = renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "Tweedie" }))
      expect(props.onUpdate).toHaveBeenCalledWith({
        loss_function: "Tweedie",
        task: "regression",
        metrics: ["gini", "tweedie_deviance"],
        variance_power: 1.5,
      })
    })

    it("clicking RMSE loss button sets loss_function to RMSE", () => {
      const { props } = renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "catboost",
          loss_function: "MAE",
        },
      })
      const rmseButtons = screen.getAllByRole("button", { name: "RMSE" })
      // Click the first RMSE button (the loss function one)
      fireEvent.click(rmseButtons[0])
      expect(props.onUpdate).toHaveBeenCalledWith({
        loss_function: "RMSE",
        task: "regression",
        metrics: ["gini", "rmse"],
      })
    })

    it("clicking the selected loss deselects it (null)", () => {
      const { props } = renderConfig()
      const rmseButtons = screen.getAllByRole("button", { name: "RMSE" })
      fireEvent.click(rmseButtons[0])
      expect(props.onUpdate).toHaveBeenCalledWith("loss_function", null)
    })

    it("clicking MAE loss button sets loss_function to MAE", () => {
      const { props } = renderConfig()
      const maeButtons = screen.getAllByRole("button", { name: "MAE" })
      fireEvent.click(maeButtons[0])
      expect(props.onUpdate).toHaveBeenCalledWith({
        loss_function: "MAE",
        task: "regression",
        metrics: ["gini", "rmse"],
      })
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Row limit input
  // ═════════════════════════════════════════════════════════════════

  describe("Row limit input", () => {
    beforeEach(() => { defaultPane = "split" })
    it.each(["catboost", "glm"])("renders row limit first in Split for %s", (algorithm) => {
      renderConfig({ config: { _nodeId: "node_1", algorithm } })
      expect(screen.getByLabelText("Row limit")).toHaveAttribute("placeholder", "All rows")
      expect(screen.getAllByRole("heading")[0]).toHaveTextContent("Row limit")
    })

    it("changing row limit calls onUpdate with parsed integer", () => {
      const { props } = renderConfig()
      const rowLimitInput = screen.getByLabelText("Row limit")
      fireEvent.change(rowLimitInput, { target: { value: "50000" } })
      expect(props.onUpdate).toHaveBeenCalledWith("row_limit", 50000)
    })

    it("clearing row limit calls onUpdate with null", () => {
      const { props } = renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", row_limit: 50000 },
      })
      const rowLimitInput = screen.getByDisplayValue("50000")
      fireEvent.change(rowLimitInput, { target: { value: "" } })
      expect(props.onUpdate).toHaveBeenCalledWith("row_limit", null)
    })

    it("shows row count label when row limit is set", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", row_limit: 100000 },
      })
      expect(screen.getByLabelText("Row limit")).toHaveValue(100000)
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Feature exclude/include updates config
  // ═════════════════════════════════════════════════════════════════

  describe("Feature exclude/include updates config", () => {
    beforeEach(() => { defaultPane = "features" })
    it("excluding multiple columns accumulates in exclude array", () => {
      const { props } = renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", exclude: ["age"] },
      })
      fireEvent.click(
        within(screen.getByRole("group", { name: "region feature" })).getByRole(
          "checkbox",
          { name: "Include region" },
        ),
      )
      expect(props.onUpdate).toHaveBeenCalledWith({ exclude: ["age", "region"] })
    })

    it("including a column from exclude list removes only that column", () => {
      const { props } = renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", exclude: ["age", "region"] },
      })
      fireEvent.click(
        within(screen.getByRole("group", { name: "region feature" })).getByRole(
          "checkbox",
          { name: "Include region" },
        ),
      )
      expect(props.onUpdate).toHaveBeenCalledWith({ exclude: ["age"] })
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Split strategy buttons
  // ═════════════════════════════════════════════════════════════════

  describe("Split strategy selection", () => {
    beforeEach(() => { defaultPane = "split" })
    it("clicking grouped evaluation calls onUpdate with group strategy", () => {
      const { props } = renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "Group split" }))
      expect(props.onUpdate).toHaveBeenCalledWith(
        "evaluation",
        expect.objectContaining({ strategy: "group" }),
      )
    })

    it("clicking random rows after temporal reverts strategy", () => {
      const { props } = renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "catboost",
          evaluation: {
            schema_version: 1,
            strategy: "temporal",
            date_column: "date",
            validation: { method: "single", start: "2025-01-01" },
          },
        },
      })
      fireEvent.click(screen.getByRole("button", { name: "Random split" }))
      expect(props.onUpdate).toHaveBeenCalledWith(
        "evaluation",
        expect.objectContaining({ strategy: "random" }),
      )
    })
  })

  describe("MOD-M10 exclusive-pane contract", () => {
    it("shows only the algorithm gateway for an unset algorithm and rejects unsupported algorithms", () => {
      renderConfig({ config: { _nodeId: "node_1", target: "loss_ratio" } })
      expect(screen.getByText("Select algorithm")).toBeTruthy()
      expect(screen.queryByRole("tabpanel")).toBeNull()
      cleanup()

      renderConfig({ config: { _nodeId: "node_1", algorithm: "xgboost" } })
      expect(screen.getByRole("alert")).toHaveTextContent("Unsupported modelling algorithm: xgboost.")
    })

    it("keeps the selected algorithm immutable and renders exactly one owning pane for both algorithms", () => {
      for (const algorithm of ["catboost", "glm"] as const) {
        for (const pane of ["target", "features", "params", "split", "train", "export"] as const) {
          if (algorithm === "glm" && pane === "params") continue
          const { unmount } = renderConfig({ activePane: pane, config: { _nodeId: "node_1", algorithm, target: "loss_ratio", loss_function: "RMSE" } })
          expect(screen.getByRole("tabpanel")).toHaveAttribute("id", `modelling-${pane}-pane`)
          expect(screen.queryByRole("button", { name: "CatBoost" })).toBeNull()
          expect(screen.queryByRole("button", { name: "GLM" })).toBeNull()
          unmount()
        }
      }
    })

    it("keeps invalid fixed JSON across navigation but resets the draft for a different node", () => {
      const { rerender, props } = renderConfig({ activePane: "params" })
      const json = screen.getByLabelText("CatBoost hyperparameters JSON")
      fireEvent.change(json, { target: { value: "{invalid" } })
      expect(screen.getByRole("alert")).toHaveTextContent("Parameters JSON")
      rerender(<GraphProvider allNodes={[]} edges={[]}><ModellingConfig {...props} activePane="train" /></GraphProvider>)
      rerender(<GraphProvider allNodes={[]} edges={[]}><ModellingConfig {...props} activePane="params" /></GraphProvider>)
      expect(screen.getByLabelText("CatBoost hyperparameters JSON")).toHaveValue("{invalid")
      rerender(<GraphProvider allNodes={[]} edges={[]}><ModellingConfig {...props} activePane="params" config={{ ...props.config, _nodeId: "node_2", params: { depth: 8 } }} /></GraphProvider>)
      expect(screen.getByLabelText("CatBoost hyperparameters JSON")).toHaveValue(JSON.stringify({ depth: 8 }, null, 2))
    })

    it("surfaces an invalid fixed-parameter draft only when Train is pressed", () => {
      const { rerender, props } = renderConfig({ activePane: "params" })

      fireEvent.change(screen.getByLabelText("CatBoost hyperparameters JSON"), {
        target: { value: "{invalid" },
      })
      expect(screen.getByRole("alert")).toHaveTextContent("Parameters JSON")

      rerender(
        <GraphProvider allNodes={[]} edges={[]}>
          <ModellingConfig {...props} activePane="train" />
        </GraphProvider>,
      )
      const trainButton = screen.getByRole("button", { name: "Train Model" })
      expect(screen.getByRole("alert")).toHaveTextContent("Parameters JSON")

      fireEvent.click(trainButton)

      expect(screen.getByRole("alert")).toHaveTextContent(
        "Parameters JSON is invalid",
      )
      expect(mockTrainModel).not.toHaveBeenCalled()
    })

    it("surfaces an invalid tuning search-space draft only when Tune & Train is pressed", () => {
      const tuning = {
        schema_version: 1,
        trial_count: 20,
        seed: 42,
        metric: "gini",
        search_space: { depth: [4, 6, 8, 10] },
      }
      const { rerender, props } = renderConfig({
        activePane: "params",
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "catboost",
          loss_function: "RMSE",
          metrics: ["gini"],
          tuning,
        },
      })

      fireEvent.change(screen.getByLabelText("CatBoost search space JSON"), {
        target: { value: "{invalid" },
      })
      expect(screen.getByRole("alert")).toHaveTextContent("Search space JSON")

      rerender(
        <GraphProvider allNodes={[]} edges={[]}>
          <ModellingConfig {...props} activePane="train" />
        </GraphProvider>,
      )
      const trainButton = screen.getByRole("button", { name: "Tune & Train" })
      expect(screen.getByRole("alert")).toHaveTextContent("Search space JSON")

      fireEvent.click(trainButton)

      expect(screen.getByRole("alert")).toHaveTextContent(
        "Search space JSON is invalid",
      )
      expect(mockTrainModel).not.toHaveBeenCalled()
    })

    it("autosaves fixed params exactly while Train owns GPU, Split owns row limit and Export owns MLflow", () => {
      const { rerender, props } = renderConfig({ activePane: "params", config: { _nodeId: "node_1", algorithm: "catboost", params: { depth: 6, task_type: "GPU" } } })
      fireEvent.change(screen.getByLabelText("CatBoost hyperparameters JSON"), { target: { value: '{"iterations":200,"custom":true}' } })
      expect(props.onUpdate).toHaveBeenCalledWith("params", {
        iterations: 200,
        custom: true,
        task_type: "GPU",
      })
      expect(screen.queryByRole("button", { name: "Apply" })).toBeNull()
      expect(screen.queryByRole("button", { name: "Revert" })).toBeNull()
      rerender(<GraphProvider allNodes={[]} edges={[]}><ModellingConfig {...props} activePane="train" /></GraphProvider>)
      expect(screen.getByRole("checkbox", { name: /GPU training/ })).toBeTruthy()
      expect(screen.queryByLabelText("Row limit")).toBeNull()
      expect(screen.queryByLabelText("MLflow experiment path")).toBeNull()
      expect(screen.queryByRole("radiogroup", { name: "MLflow destination" })).toBeNull()
      rerender(<GraphProvider allNodes={[]} edges={[]}><ModellingConfig {...props} activePane="split" /></GraphProvider>)
      expect(screen.getByLabelText("Row limit")).toBeTruthy()
      expect(screen.queryByRole("checkbox", { name: /GPU training/ })).toBeNull()
      rerender(<GraphProvider allNodes={[]} edges={[]}><ModellingConfig {...props} activePane="export" /></GraphProvider>)
      expect(screen.queryByLabelText("Row limit")).toBeNull()
      expect(screen.getByLabelText("MLflow experiment path")).toBeTruthy()
      expect(screen.queryByLabelText("MLflow model name")).toBeNull()
      expect(screen.getByRole("radiogroup", { name: "MLflow destination" })).toBeTruthy()
      expect(screen.getByLabelText("Filename or path *")).toBeTruthy()
    })

    describe("MLflow section", () => {
      beforeEach(async () => {
        mockGetExperiments.mockReset().mockResolvedValue([])
        const { default: useUIStore } = await import("../../stores/useUIStore")
        useUIStore.setState({ mlflowSettingsOpen: false })
      })

      /** Text anywhere in the pane EXCEPT inside a tooltip. */
      function proseText(pattern: RegExp) {
        return screen.queryByText(pattern, { ignore: "[role='tooltip'],script,style" })
      }

      /** Hover the icon's Tooltip wrapper; read the bubble the icon itself is described by. */
      function tooltipTextOf(ariaLabel: string): string {
        const icon = screen.getByLabelText(ariaLabel)
        fireEvent.mouseEnter(icon.parentElement!)
        return document.getElementById(icon.getAttribute("aria-describedby")!)!.textContent ?? ""
      }

      it("keeps the manual-only note in the heading tooltip, not in the pane", () => {
        renderConfig({ activePane: "export" })
        expect(proseText(/nothing is logged automatically/i)).toBeNull()
        expect(tooltipTextOf("About MLflow logging")).toMatch(/nothing is logged automatically/i)
      })

      it("mounts the destination selector and drops the logging-destination line", () => {
        renderConfig({ activePane: "export" })
        expect(screen.getByRole("radiogroup", { name: "MLflow destination" })).toBeTruthy()
        expect(proseText(/Logging destination/i)).toBeNull()
        expect(proseText(/toolbar/i)).toBeNull()
      })

      it("names the Databricks default experiment path when the node chooses Databricks", () => {
        setMlflowInventory({
          destinations: [MLFLOW_DATABRICKS, MLFLOW_SERVER, MLFLOW_LOCAL],
        })
        renderConfig({
          activePane: "export",
          config: { _nodeId: "node_1", algorithm: "catboost", mlflow_destination: "databricks" },
        })
        const help = tooltipTextOf("About the experiment path")
        expect(help).toContain("Leave blank to use /Shared/haute/model.")
        expect(help).toContain("named group")
        expect(help).toContain("workspace folder path")
        expect(screen.getByLabelText("About the experiment path")).toHaveAccessibleDescription(
          /named group/,
        )
        expect(screen.getByLabelText("MLflow experiment path")).toHaveAttribute(
          "placeholder",
          "/Shared/haute/model",
        )
      })

      it("names the bare node label when the node names no destination under the same inventory", () => {
        setMlflowInventory({
          destinations: [MLFLOW_DATABRICKS, MLFLOW_SERVER, MLFLOW_LOCAL],
        })
        renderConfig({
          activePane: "export",
          config: { _nodeId: "node_1", algorithm: "catboost" },
        })
        const help = tooltipTextOf("About the experiment path")
        expect(help).toContain("Leave blank to use model.")
        expect(help).toContain("named group")
        expect(help).toContain("workspace folder path")
        expect(screen.getByLabelText("MLflow experiment path")).toHaveAttribute(
          "placeholder",
          "model",
        )
      })

      it("offers no model registry field: haute logs candidate runs and never registers them", () => {
        renderConfig({ activePane: "export" })
        expect(screen.queryByLabelText("MLflow model name")).toBeNull()
        expect(screen.queryByLabelText("About the model name")).toBeNull()
        expect(proseText(/regist/i)).toBeNull()
      })

      it("keeps a node without a destination on the local folder whatever else is configured", () => {
        const trainConfig = { _nodeId: "node_1", algorithm: "catboost" }
        setMlflowInventory({
          destinations: [MLFLOW_SERVER, MLFLOW_LOCAL],
        })
        renderConfig({ activePane: "export", config: trainConfig })
        expect(screen.getByRole("radio", { name: /Local folder/ })).toBeChecked()
        cleanup()

        setMlflowInventory({
          destinations: [MLFLOW_DATABRICKS, MLFLOW_SERVER, MLFLOW_LOCAL],
        })
        renderConfig({ activePane: "export", config: trainConfig })
        expect(screen.getByRole("radio", { name: /Local folder/ })).toBeChecked()
        expect(screen.getByRole("radio", { name: /Databricks/ })).not.toBeChecked()
        expect(screen.queryByText(/C:\/proj\/mlruns/)).toBeNull()
        expect(screen.queryByRole("button", { name: /auto/i })).toBeNull()
      })

      it("writes a remote choice to the node config and removes the key for Local folder", () => {
        setMlflowInventory({
          destinations: [MLFLOW_DATABRICKS, MLFLOW_SERVER, MLFLOW_LOCAL],
        })
        const { props } = renderConfig({ activePane: "export" })
        fireEvent.click(screen.getByRole("radio", { name: /MLflow server/ }))
        expect(props.onUpdate).toHaveBeenCalledWith("mlflow_destination", "server")

        cleanup()
        const chosen = renderConfig({
          activePane: "export",
          config: { _nodeId: "node_1", algorithm: "catboost", mlflow_destination: "databricks" },
        })
        fireEvent.click(screen.getByRole("radio", { name: /Local folder/ }))
        expect(chosen.props.onUpdate).toHaveBeenCalledWith("mlflow_destination", undefined)
      })

      it("browses the node's own destination for experiment suggestions", async () => {
        setMlflowInventory({
          destinations: [MLFLOW_DATABRICKS, MLFLOW_SERVER, MLFLOW_LOCAL],
        })
        mockGetExperiments.mockResolvedValue([{ experiment_id: "1", name: "local-exp" }])
        renderConfig({
          activePane: "export",
          config: { _nodeId: "node_1", algorithm: "catboost", mlflow_destination: "server" },
        })
        fireEvent.focus(screen.getByLabelText("MLflow experiment path"))
        await waitFor(() => {
          expect(mockGetExperiments).toHaveBeenCalledWith("server")
          expect(document.querySelector("datalist option[value='local-exp']")).toBeTruthy()
        })
      })

      it("browses the local folder with an empty destination", async () => {
        mockGetExperiments.mockResolvedValue([
          { experiment_id: "1", name: "/Shared/haute/team-exp" },
        ])
        renderConfig({ activePane: "export" })
        fireEvent.focus(screen.getByLabelText("MLflow experiment path"))
        await waitFor(() => {
          expect(mockGetExperiments).toHaveBeenCalledWith("")
          expect(document.querySelector("datalist option[value='/Shared/haute/team-exp']")).toBeTruthy()
        })
      })

      it("does not browse when the node's destination cannot accept a log", () => {
        setMlflowInventory({
          destinations: [
            mlflowEntry("server", { configured: false, config_source: "", detail: "Set [mlflow] tracking_uri." }),
            MLFLOW_LOCAL,
          ],
        })
        renderConfig({
          activePane: "export",
          config: { _nodeId: "node_1", algorithm: "catboost", mlflow_destination: "server" },
        })
        fireEvent.focus(screen.getByLabelText("MLflow experiment path"))
        expect(mockGetExperiments).not.toHaveBeenCalled()
      })

      it("invalidates suggestions and discards stale responses on a destination switch", async () => {
        setMlflowInventory({
          destinations: [MLFLOW_SERVER, MLFLOW_LOCAL],
        })
        let resolveFirst: (value: { experiment_id: string; name: string }[]) => void
        mockGetExperiments.mockReturnValueOnce(
          new Promise((resolve) => {
            resolveFirst = resolve
          }),
        )
        renderConfig({
          activePane: "export",
          config: { _nodeId: "node_1", algorithm: "catboost", mlflow_destination: "server" },
        })
        fireEvent.focus(screen.getByLabelText("MLflow experiment path"))
        expect(mockGetExperiments).toHaveBeenCalledTimes(1)

        // The workspace repoints the server while the first fetch is in flight.
        act(() => {
          setMlflowInventory({
            destinations: [
              mlflowEntry("server", { destination: "http://other:5000", config_source: "toml", probed: true, ok: true }),
              MLFLOW_LOCAL,
            ],
          })
        })
        // Resolve the stale request and let every resulting update settle
        // BEFORE asserting absence — a not-yet-rendered option must not be
        // what makes this pass.
        await act(async () => {
          resolveFirst!([{ experiment_id: "9", name: "stale-exp" }])
        })
        expect(document.querySelector("datalist option[value='stale-exp']")).toBeNull()

        mockGetExperiments.mockResolvedValueOnce([
          { experiment_id: "2", name: "fresh-exp" },
        ])
        fireEvent.focus(screen.getByLabelText("MLflow experiment path"))
        await waitFor(() => {
          expect(mockGetExperiments).toHaveBeenCalledTimes(2)
          expect(document.querySelector("datalist option[value='fresh-exp']")).toBeTruthy()
        })
        expect(document.querySelector("datalist option[value='stale-exp']")).toBeNull()
      })

      it("clears already populated suggestions when the destination changes", async () => {
        setMlflowInventory({
          destinations: [MLFLOW_SERVER, MLFLOW_LOCAL],
        })
        mockGetExperiments.mockResolvedValueOnce([
          { experiment_id: "1", name: "old-exp" },
        ])
        renderConfig({
          activePane: "export",
          config: { _nodeId: "node_1", algorithm: "catboost", mlflow_destination: "server" },
        })
        fireEvent.focus(screen.getByLabelText("MLflow experiment path"))
        await waitFor(() => {
          expect(document.querySelector("datalist option[value='old-exp']")).toBeTruthy()
        })

        act(() => {
          setMlflowInventory({
            destinations: [
              mlflowEntry("server", { destination: "http://other:5000", config_source: "toml", probed: true, ok: true }),
              MLFLOW_LOCAL,
            ],
          })
        })
        expect(document.querySelector("datalist option[value='old-exp']")).toBeNull()
      })
    })

    it("uses the standard themed form styling throughout the Split, Train and Export panes", () => {
      const { rerender, props } = renderConfig({ activePane: "train" })

      const gpu = screen.getByRole("checkbox", { name: /GPU training/ })
      expect(gpu).toHaveClass("accent-purple-500")
      rerender(<GraphProvider allNodes={[]} edges={[]}><ModellingConfig {...props} activePane="split" /></GraphProvider>)
      const rowLimit = screen.getByLabelText("Row limit")
      expect(rowLimit).toHaveAttribute("placeholder", "All rows")
      expect(rowLimit).toHaveClass("w-full", "font-mono")
      expectThemedField(rowLimit)

      rerender(<GraphProvider allNodes={[]} edges={[]}><ModellingConfig {...props} activePane="export" /></GraphProvider>)
      const experiment = screen.getByLabelText("MLflow experiment path")
      expectThemedField(experiment)
      expect(experiment).toHaveClass("w-full", "rounded-lg", "font-mono")
    })

    function expectThemedField(field: HTMLElement) {
      expect(field).toHaveStyle({
        background: "var(--bg-input)",
        color: "var(--text-primary)",
      })
      expect(field.getAttribute("style")).toContain("border: 1px solid var(--border)")
    }

    describe("Export pane", () => {
      const EXPORT_BLOCKED_NO_MODEL = "Train this model to export it."
      const EXPORT_BLOCKED_TRAINING = "Training is running — export is available when it completes."
      const EXPORT_STALE =
        "Training settings changed since this model was trained. Exports use the last trained model."

      function seedTrainedResult(
        config: Record<string, unknown>,
        overrides: { result?: TrainResult; configHash?: string } = {},
      ) {
        useNodeResultsStore.setState({
          trainResults: {
            node_1: {
              result: overrides.result ?? makeTrainResult(),
              jobId: "job_1",
              configHash: overrides.configHash ?? hashConfig(trainingIdentityConfig(config)),
              source: "live",
              structuralVersion: 0,
            },
          },
        })
      }

      function actionButtons() {
        return {
          log: screen.getByRole("button", { name: "Log run to MLflow" }),
          save: screen.getByRole("button", { name: "Save model to file" }),
        }
      }

      it("keeps both actions disabled without a train-first note when no model is available", () => {
        renderConfig({ activePane: "export" })
        expect(screen.queryByText(EXPORT_BLOCKED_NO_MODEL)).toBeNull()
        const { log, save } = actionButtons()
        expect(log).toBeDisabled()
        expect(save).toBeDisabled()
        fireEvent.click(save)
        expect(mockSaveTrainedModel).not.toHaveBeenCalled()
      })

      it("treats a failed training result as no exportable model", () => {
        seedTrainedResult(defaultProps().config, {
          result: makeTrainResult({ status: "error", error: "OOM" }),
        })
        renderConfig({ activePane: "export" })
        expect(screen.queryByText(EXPORT_BLOCKED_NO_MODEL)).toBeNull()
        expect(actionButtons().save).toBeDisabled()
      })

      it("disables both actions while a training job for the node runs", () => {
        const config = defaultProps().config
        seedTrainedResult(config)
        useNodeResultsStore.setState({
          trainJobs: {
            node_1: {
              jobId: "job_2",
              nodeId: "node_1",
              nodeLabel: "Model",
              progress: null,
              error: null,
              configHash: hashConfig(config),
              source: "live",
              structuralVersion: 0,
            },
          },
        })
        renderConfig({ activePane: "export" })
        expect(screen.getByText(EXPORT_BLOCKED_TRAINING)).toBeTruthy()
        expect(screen.queryByText(EXPORT_BLOCKED_NO_MODEL)).toBeNull()
        const { log, save } = actionButtons()
        expect(log).toBeDisabled()
        expect(save).toBeDisabled()
      })

      it("enables both actions without notes for a current trained model", () => {
        const config = { ...defaultProps().config, model_export_path: "frequency" }
        seedTrainedResult(config)
        renderConfig({ activePane: "export", config })
        expect(screen.queryByText(EXPORT_BLOCKED_NO_MODEL)).toBeNull()
        expect(screen.queryByText(EXPORT_STALE)).toBeNull()
        const { log, save } = actionButtons()
        expect(log).toBeEnabled()
        expect(save).toBeEnabled()
      })

      it("warns that exports use the last trained model after training settings change", () => {
        const config = { ...defaultProps().config, model_export_path: "frequency" }
        seedTrainedResult(config, { configHash: "hash_before_target_change" })
        renderConfig({ activePane: "export", config })
        expect(screen.getByText(EXPORT_STALE)).toBeTruthy()
        expect(actionButtons().save).toBeEnabled()
      })

      it("keeps the trained result current and skips a RAM re-estimate when only export fields change", () => {
        const exportFields = {
          mlflow_destination: "databricks",
          mlflow_experiment: "pricing",
          model_export_path: "models/frequency.cbm",
        }
        // The graph store holds the node config without the panel-injected node id.
        const storedTrainingConfig: Record<string, unknown> = { ...defaultProps().config }
        delete storedTrainingConfig._nodeId
        const modellingNode = (config: Record<string, unknown>) => ({
          id: "node_1",
          type: "modelling",
          position: { x: 0, y: 0 },
          data: { label: "model", nodeType: "modelling", config },
        })
        // The canvas edit path: the node's stored config changes in the graph store.
        const updateStoredConfig = (config: Record<string, unknown>) =>
          act(() => useGraphStore.getState().setNodesRaw([modellingNode(config)]))
        updateStoredConfig({ ...storedTrainingConfig, ...exportFields })
        const trainedVersion = useGraphStore.getState().structuralVersion
        useNodeResultsStore.setState({
          trainResults: {
            node_1: {
              result: makeTrainResult(),
              jobId: "job_1",
              configHash: hashConfig(trainingIdentityConfig(defaultProps().config)),
              source: "live",
              structuralVersion: trainedVersion,
            },
          },
        })
        const { rerender, props } = renderConfig({
          activePane: "train",
          config: { ...defaultProps().config, ...exportFields },
        })
        expect(screen.queryByText("Config changed since last training")).toBeNull()
        const estimateCalls = mockEstimateTrainingRam.mock.calls.length
        const renderWith = (config: Record<string, unknown>) =>
          rerender(
            <GraphProvider allNodes={[]} edges={[]}>
              <ModellingConfig {...props} activePane="train" config={{ ...props.config, ...config }} />
            </GraphProvider>,
          )

        const renamed = { mlflow_destination: undefined, mlflow_experiment: "renamed", model_export_path: "models/renamed.cbm" }
        updateStoredConfig({ ...storedTrainingConfig, ...renamed })
        renderWith(renamed)

        expect(useGraphStore.getState().structuralVersion).toBe(trainedVersion)
        expect(screen.queryByText("Config changed since last training")).toBeNull()
        expect(mockEstimateTrainingRam).toHaveBeenCalledTimes(estimateCalls)

        // A training field edited the same way does change the identity.
        updateStoredConfig({ ...storedTrainingConfig, ...renamed, row_limit: 5000 })
        expect(useGraphStore.getState().structuralVersion).toBe(trainedVersion + 1)
        renderWith({ ...renamed, row_limit: 5000 })
        expect(screen.getByText("Config changed since last training")).toBeTruthy()
      })

      describe("Model file", () => {
        const TRAINED_CONFIG = () => ({ ...defaultProps().config, model_export_path: "frequency" })

        /** The client mock's ApiError ignores constructor fields, so set them explicitly. */
        function apiError(status: number, detail: string | Record<string, unknown>) {
          const text = typeof detail === "string" ? detail : JSON.stringify(detail)
          const error = new ApiError(`HTTP ${status}`, status, text)
          return Object.assign(error, { status, detail: text, rawDetail: detail })
        }

        it("requires a filename or path and retains the folder browser without instructions", async () => {
          seedTrainedResult(defaultProps().config)
          renderConfig({ activePane: "export" })

          expect(screen.getByLabelText("Filename or path *")).toHaveValue("")
          expect(screen.queryByText(/Filenames save in the project's models/)).toBeNull()
          expect(await screen.findByText("No matching files")).toBeTruthy()
          expect(actionButtons().save).toBeDisabled()
          expect(mockResolveModelSaveDestination).not.toHaveBeenCalled()
        })

        it("commits the filename or path to the node config", () => {
          const { props } = renderConfig({ activePane: "export" })
          const field = screen.getByLabelText("Filename or path *")
          fireEvent.change(field, { target: { value: "severity" } })
          expect(props.onUpdate).not.toHaveBeenCalled()
          fireEvent.blur(field)
          expect(props.onUpdate).toHaveBeenCalledWith("model_export_path", "severity")
        })

        it("shows a resolved destination when it adds the folder and model extension", async () => {
          const glmConfig = {
            _nodeId: "node_1",
            algorithm: "glm",
            target: "loss_ratio",
            model_export_path: "severity",
          }
          mockResolveModelSaveDestination.mockResolvedValue({
            path: "models/severity.rsglm",
            suffix_mismatch: false,
          })
          await act(async () => {
            renderConfig({ activePane: "export", config: glmConfig })
          })

          expect(screen.getByText("severity")).toBeTruthy()
          expect(screen.getByText("Destination: models/severity.rsglm")).toBeTruthy()
          fireEvent.click(screen.getByTestId("file-change-btn"))
          expect(screen.getByLabelText("Filename or path *")).toHaveValue("severity")
          expect(mockResolveModelSaveDestination).toHaveBeenCalledWith(
            { output_path: "severity", algorithm: "glm" },
            expect.objectContaining({ signal: expect.any(AbortSignal) }),
          )
          expect(screen.queryByText(/The model's \.rsglm extension is added if omitted\./)).toBeNull()
        })

        it.each(["models/frequency.cbm", "models\\frequency.cbm"])("shows selected filepath %s once without repeating its destination", async (path) => {
          mockResolveModelSaveDestination.mockResolvedValue({ path: "models/frequency.cbm", suffix_mismatch: false })
          await act(async () => {
            renderConfig({ activePane: "export", config: { ...defaultProps().config, model_export_path: path } })
          })

          expect(screen.getAllByText(path)).toHaveLength(1)
          expect(screen.queryByText("Destination: models/frequency.cbm")).toBeNull()
        })

        it("blocks saving when the destination extension does not match the model format", async () => {
          const config = { ...defaultProps().config, model_export_path: "frequency.parquet" }
          seedTrainedResult(config)
          mockResolveModelSaveDestination.mockResolvedValue({
            path: "models/frequency.parquet",
            suffix_mismatch: true,
          })
          renderConfig({ activePane: "export", config })

          expect(
            await screen.findByText("The destination extension does not match the model format (.cbm)."),
          ).toBeTruthy()
          expect(actionButtons().save).toBeDisabled()
        })

        it("reports a destination the server cannot resolve", async () => {
          mockResolveModelSaveDestination.mockRejectedValue(
            apiError(403, "Path '../x' resolves outside the project root"),
          )
          renderConfig({
            activePane: "export",
            config: { ...defaultProps().config, model_export_path: "../x" },
          })

          expect(
            await screen.findByText(
              "Could not resolve destination: Path '../x' resolves outside the project root",
            ),
          ).toBeTruthy()
        })

        it("saves without overwrite and names both written files", async () => {
          const config = TRAINED_CONFIG()
          seedTrainedResult(config)
          mockSaveTrainedModel.mockResolvedValue({
            status: "ok",
            path: "models/frequency.cbm",
            feature_contract_path: "models/frequency.feature_contract.json",
          })
          renderConfig({ activePane: "export", config })

          fireEvent.click(actionButtons().save)

          expect(mockSaveTrainedModel).toHaveBeenCalledWith({
            job_id: "job_1",
            output_path: "frequency",
            overwrite: false,
          })
          expect(await screen.findByText("Saved model to models/frequency.cbm")).toBeTruthy()
          expect(screen.getByText("Feature contract: models/frequency.feature_contract.json")).toBeTruthy()
        })

        it("asks before replacing an existing file and only then saves with overwrite", async () => {
          const config = TRAINED_CONFIG()
          seedTrainedResult(config)
          mockSaveTrainedModel
            .mockRejectedValueOnce(
              apiError(409, {
                error_code: "model_file_exists",
                message: "Model file already exists: models/frequency.cbm",
              }),
            )
            .mockReturnValueOnce(new Promise(() => {}))
          renderConfig({ activePane: "export", config })

          fireEvent.click(actionButtons().save)
          expect(await screen.findByText("Model file already exists: models/frequency.cbm")).toBeTruthy()
          expect(mockSaveTrainedModel).toHaveBeenCalledTimes(1)

          fireEvent.click(screen.getByRole("button", { name: "Replace existing file" }))

          expect(mockSaveTrainedModel).toHaveBeenLastCalledWith({
            job_id: "job_1",
            output_path: "frequency",
            overwrite: true,
          })
          expect(screen.getByRole("button", { name: "Saving..." })).toBeDisabled()
          expect(screen.queryByRole("button", { name: "Replace existing file" })).toBeNull()
        })

        it("shows the server's detail when saving fails and hides it once the path changes", async () => {
          const config = TRAINED_CONFIG()
          seedTrainedResult(config)
          mockSaveTrainedModel.mockRejectedValueOnce(
            apiError(410, {
              error_code: "training_artifacts_unavailable",
              message: "The trained model files are no longer on disk; retrain the model before saving it.",
            }),
          )
          const { rerender, props } = renderConfig({ activePane: "export", config })

          fireEvent.click(actionButtons().save)
          const message = "The trained model files are no longer on disk; retrain the model before saving it."
          expect(await screen.findByText(message)).toBeTruthy()
          expect(screen.queryByRole("button", { name: "Replace existing file" })).toBeNull()

          rerender(
            <GraphProvider allNodes={[]} edges={[]}>
              <ModellingConfig {...props} config={{ ...config, model_export_path: "severity" }} />
            </GraphProvider>,
          )
          expect(screen.queryByText(message)).toBeNull()
        })
      })

      it("logs the last trained job to MLflow from the Export pane", async () => {
        seedTrainedResult(defaultProps().config)
        mockLogToMlflow.mockResolvedValue({
          status: "ok",
          backend: "local",
          experiment_name: "model",
          run_id: "run-1",
          run_url: null,
          tracking_uri: "file:///C:/proj/mlruns",
          error: null,
        })
        renderConfig({ activePane: "export" })
        fireEvent.click(actionButtons().log)
        await waitFor(() => {
          expect(mockLogToMlflow).toHaveBeenCalledWith(
            expect.objectContaining({ job_id: "job_1", destination: "" }),
          )
        })
        expect(await screen.findByTestId("mlflow-log-success")).toHaveTextContent("Run ID: run-1")
      })

      describe("receipts and reload", () => {
        const RECEIPT = {
          operation_id: "op-1",
          destination: "",
          backend: "local",
          experiment_name: "model",
          run_id: "run-42",
          run_url: null,
          tracking_uri: "file:///C:/proj/mlruns",
          logged_at: "2026-09-13T08:30:00+00:00",
        }

        function completedStatus(overrides: Record<string, unknown> = {}) {
          return {
            status: "completed",
            progress: 1,
            message: "",
            iteration: 0,
            total_iterations: 0,
            train_loss: {},
            elapsed_seconds: 1,
            result: makeTrainResult(),
            export_receipts: { mlflow: [RECEIPT], model_files: [{ path: "models/frequency.cbm", feature_contract_path: "models/frequency.feature_contract.json", saved_at: "2026-09-13T08:31:00+00:00" }] },
            ...overrides,
          }
        }

        it("says where the result was last logged and saved and asks before logging again", async () => {
          const config = { ...defaultProps().config, model_export_path: "frequency" }
          seedTrainedResult(config)
          mockGetTrainStatus.mockResolvedValue(completedStatus())
          mockResolveModelSaveDestination.mockResolvedValue({ path: "models/frequency.cbm", suffix_mismatch: false })
          renderConfig({ activePane: "export", config })

          expect(await screen.findByTestId("mlflow-last-logged")).toHaveTextContent(
            "Last logged to Local folder · model · run run-42",
          )
          expect(screen.getByTestId("model-file-last-saved")).toHaveTextContent(
            "Last saved to models/frequency.cbm",
          )
          expect(mockGetTrainStatus).toHaveBeenCalledWith("job_1", expect.anything())

          fireEvent.click(screen.getByRole("button", { name: "Log again" }))
          expect(mockLogToMlflow).not.toHaveBeenCalled()
          expect(screen.getByText("This result is already logged to model. Logging again creates a new run.")).toBeTruthy()
          mockLogToMlflow.mockResolvedValue({ status: "ok", backend: "local", experiment_name: "model", run_id: "run-43", run_url: null, tracking_uri: "file:///C:/proj/mlruns", error: null })
          fireEvent.click(screen.getByRole("button", { name: "Log as a new run" }))
          await waitFor(() => expect(mockLogToMlflow).toHaveBeenCalledTimes(1))
          const operationId = mockLogToMlflow.mock.calls[0][0].operation_id
          expect(operationId).not.toBe(RECEIPT.operation_id)
          expect(typeof operationId).toBe("string")
          // The receipts are re-read after the attempt.
          await waitFor(() => expect(mockGetTrainStatus).toHaveBeenCalledTimes(2))
        })

        it("restores a remembered result after a reload through the job status", async () => {
          const config = defaultProps().config
          useDocumentStatusStore.setState({ sourceFile: "rating/main.py" })
          const { writeTrainedJobHandle } = await import("../../utils/trainedJobHandles")
          writeTrainedJobHandle("rating/main.py", "node_1", {
            jobId: "job_restored",
            configHash: hashConfig(trainingIdentityConfig(config)),
            source: "live",
            // The (mocked) empty graph this editor renders against.
            lineage: trainingLineage({ nodes: [], edges: [], preamble: "" }),
          })
          mockGetTrainStatus.mockResolvedValue(completedStatus())

          renderConfig({ activePane: "export", config })

          await waitFor(() =>
            expect(useNodeResultsStore.getState().trainResults.node_1?.jobId).toBe("job_restored"),
          )
          expect(mockGetTrainStatus).toHaveBeenCalledWith("job_restored", expect.anything())
          expect(screen.getByRole("button", { name: "Log again" })).toBeEnabled()
          expect(screen.queryByText(EXPORT_STALE)).toBeNull()
        })

        it("restores a result trained against a different graph as stale", async () => {
          const config = defaultProps().config
          useDocumentStatusStore.setState({ sourceFile: "rating/main.py" })
          const { writeTrainedJobHandle } = await import("../../utils/trainedJobHandles")
          const upstream = { id: "source", data: { label: "source", description: "", nodeType: "polars", code: "df" } }
          writeTrainedJobHandle("rating/main.py", "node_1", {
            jobId: "job_restored",
            configHash: hashConfig(trainingIdentityConfig(config)),
            source: "live",
            lineage: trainingLineage(payloadFor([upstream])),
          })
          mockGetTrainStatus.mockResolvedValue(completedStatus())
          withPassThroughGraph()

          // The upstream transform was edited and saved after training; then the page reloaded.
          renderConfig({
            activePane: "export",
            config,
            allNodes: [{ ...upstream, data: { ...upstream.data, code: "df.head(10)" } }],
          })

          await waitFor(() =>
            expect(useNodeResultsStore.getState().trainResults.node_1?.jobId).toBe("job_restored"),
          )
          expect(await screen.findByText(EXPORT_STALE)).toBeTruthy()
          expect(screen.getByRole("button", { name: "Log again" })).toBeEnabled()
        })

        it("restores a result as stale after a submodel's internal transform changed", async () => {
          const config = defaultProps().config
          useDocumentStatusStore.setState({ sourceFile: "rating/main.py" })
          const { writeTrainedJobHandle } = await import("../../utils/trainedJobHandles")
          const submodels = (code: string) => ({
            features: {
              definitionId: "features",
              file: "submodels/features.py",
              inputPorts: [],
              outputPorts: [],
              graph: { nodes: [{ id: "clean", data: { label: "clean", nodeType: "polars", code } }], edges: [] },
            },
          })
          writeTrainedJobHandle("rating/main.py", "node_1", {
            jobId: "job_restored",
            configHash: hashConfig(trainingIdentityConfig(config)),
            source: "live",
            lineage: trainingLineage(payloadFor([], submodels("df"))),
          })
          mockGetTrainStatus.mockResolvedValue(completedStatus())
          withPassThroughGraph()

          renderConfig({ activePane: "export", config, submodels: submodels("df.drop_nulls()") })

          await waitFor(() =>
            expect(useNodeResultsStore.getState().trainResults.node_1?.jobId).toBe("job_restored"),
          )
          expect(await screen.findByText(EXPORT_STALE)).toBeTruthy()
        })

        it("reports honestly when the remembered result is gone from the server", async () => {
          useDocumentStatusStore.setState({ sourceFile: "rating/main.py" })
          const { readTrainedJobHandle, writeTrainedJobHandle } = await import("../../utils/trainedJobHandles")
          writeTrainedJobHandle("rating/main.py", "node_1", { jobId: "job_gone", configHash: "h", source: "live", lineage: "l" })
          mockGetTrainStatus.mockRejectedValue(Object.assign(new ApiError("HTTP 404", 404), { status: 404 }))

          renderConfig({ activePane: "export" })

          expect(
            await screen.findByText(
              "The last training result for this node is no longer available (the server restarted or it expired). Train this model again to export it.",
            ),
          ).toBeTruthy()
          expect(readTrainedJobHandle("rating/main.py", "node_1")).toBeNull()
          expect(useNodeResultsStore.getState().trainResults.node_1).toBeUndefined()
        })
      })
    })
  })
})
