import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react"
import ModellingConfig from "../ModellingConfig"
import { GraphProvider } from "../GraphContext"
import useNodeResultsStore, { hashConfig } from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useToastStore from "../../stores/useToastStore"
import type { ModellingPane } from "../../stores/useUIStore"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import type { SimpleNode, SimpleEdge } from "../editors"
import { makeExecutionMetricsFixture } from "../../testSupport/executionMetricsFixture"

// ── Mocks ────────────────────────────────────────────────────────

const mockTrainModel = vi.fn()
const mockCancelTrain = vi.fn()
const mockEstimateTrainingRam = vi.fn()
let defaultPane: ModellingPane = "target"

vi.mock("../../api/client", () => ({
  trainModel: (...args: unknown[]) => mockTrainModel(...args),
  cancelTrain: (...args: unknown[]) => mockCancelTrain(...args),
  estimateTrainingRam: (...args: unknown[]) => mockEstimateTrainingRam(...args),
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
  const { allNodes, edges, submodels, preamble, ...rest } = overrides
  void allNodes; void edges; void submodels; void preamble
  return {
    // The backend requires an explicit objective, so the shared default is a
    // complete configuration that can exercise successful training paths.
    config: {
      _nodeId: "node_1",
      target: "loss_ratio",
      task: "regression",
      algorithm: "catboost",
      loss_function: "RMSE",
    },
    onUpdate: vi.fn(),
    upstreamColumns: defaultColumns,
    activePane: defaultPane,
    ...rest,
  }
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
  return {
    status: "ok",
    metrics: { gini: 0.45, rmse: 0.12 },
    feature_importance: [
      { feature: "age", importance: 0.6 },
      { feature: "region", importance: 0.4 },
    ],
    model_path: "/models/catboost_model.cbm",
    train_rows: 8000,
    validation_rows: 2000,
    ...overrides,
  }
}

// ── Setup / teardown ─────────────────────────────────────────────

beforeEach(() => {
  defaultPane = "target"
  useNodeResultsStore.setState({
    trainJobs: {},
    trainResults: {},
  })
  useSettingsStore.setState({
    mlflow: {
      status: "pending",
      backend: "",
      host: "",
      installed: null,
      importable: null,
      trackingConfigured: null,
      detail: "",
    },
    openSections: {},
  })
  useToastStore.setState({ toasts: [], _toastCounter: 0 })
  mockTrainModel.mockReset()
  mockCancelTrain.mockReset()
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

describe("ModellingConfig", () => {
  describe("Config rendering", () => {
    it("renders target column dropdown with upstream columns", () => {
      renderConfig()
      // Target select: the label says "Target column", find the select within that section
      const targetLabel = screen.getByText("Target column")
      const targetSection = targetLabel.closest("div")!
      const targetSelect = targetSection.querySelector("select")!
      expect(targetSelect.value).toBe("loss_ratio")
      const options = within(targetSelect).getAllByRole("option")
      // 1 placeholder + 4 columns
      expect(options).toHaveLength(5)
      expect(options.map((o) => o.textContent)).toContain("loss_ratio (Float64)")
      expect(options.map((o) => o.textContent)).toContain("age (Int64)")
    })

    it("renders weight column dropdown with 'None' default", () => {
      renderConfig()
      // Weight defaults to "" which is the "None" option
      const weightSelect = screen.getAllByDisplayValue("None")[0]
      expect(weightSelect).toBeTruthy()
    })

    it("renders task toggle with regression active by default", () => {
      renderConfig()
      const regressionBtn = screen.getByRole("button", { name: "regression" })
      const classificationBtn = screen.getByRole("button", { name: "classification" })
      expect(regressionBtn).toBeTruthy()
      expect(classificationBtn).toBeTruthy()
    })

    it("switching task to classification calls onUpdate with new task, metrics, and clears loss", () => {
      const { props } = renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "classification" }))
      // Should call onUpdate once with merged object
      expect(props.onUpdate).toHaveBeenCalledWith({
        task: "classification",
        metrics: ["auc", "logloss"],
        loss_function: null,
      })
    })

    it("feature count shows correct number (excludes target and weight)", () => {
      renderConfig({ activePane: "features" })
      // 4 columns total. Target=loss_ratio excluded, weight="" so not excluded.
      // Feature columns: age, region, exposure = 3 of 4
      expect(screen.getAllByRole("button", { name: "Exclude" })).toHaveLength(3)
    })

    it("feature count adjusts when weight is set", () => {
      renderConfig({
        activePane: "features",
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", weight: "exposure" },
      })
      // Target=loss_ratio, weight=exposure both excluded. Features: age, region = 2 of 4
      expect(screen.getAllByRole("button", { name: "Exclude" })).toHaveLength(2)
    })

    it("exclude column toggles work", () => {
      vi.spyOn(window, "confirm").mockReturnValue(true)
      const { props } = renderConfig({ activePane: "features" })
      // Find the feature row span for "age" (not <option> elements)
      const ageSpan = screen.getAllByText("age").find(el => el.tagName === "SPAN")!
      fireEvent.click(within(ageSpan.closest("div")!).getByRole("button", { name: "Exclude" }))
      expect(props.onUpdate).toHaveBeenCalledWith({ exclude: ["age"] })
    })

    it("excluded column re-includes on second click", () => {
      const { props } = renderConfig({
        activePane: "features",
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", exclude: ["age"] },
      })
      // Find the feature row span for "age" (not <option> elements)
      const ageSpan = screen.getAllByText("age").find(el => el.tagName === "SPAN")!
      fireEvent.click(within(ageSpan.closest("div")!).getByRole("button", { name: "Include" }))
      // Should remove "age" from exclusion list
      expect(props.onUpdate).toHaveBeenCalledWith({ exclude: [] })
    })

    it("shows algorithm picker when algorithm is not set", () => {
      renderConfig({ config: { _nodeId: "node_1", target: "loss_ratio", task: "regression" } })
      expect(screen.getByText("Select Algorithm")).toBeTruthy()
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
      expect(props.onUpdate).toHaveBeenCalledWith("algorithm", "catboost")
    })

    it("loss function shows regression losses as toggle buttons for regression task", () => {
      renderConfig()
      // RMSE/MAE appear as both loss and metric buttons
      expect(screen.getAllByRole("button", { name: "RMSE" }).length).toBeGreaterThanOrEqual(1)
      expect(screen.getAllByRole("button", { name: "MAE" }).length).toBeGreaterThanOrEqual(1)
      // Poisson/Tweedie are loss-only
      expect(screen.getByRole("button", { name: "Poisson" })).toBeTruthy()
      expect(screen.getByRole("button", { name: "Tweedie" })).toBeTruthy()
      // Should NOT contain classification losses
      expect(screen.queryByRole("button", { name: "CrossEntropy" })).toBeNull()
    })

    it("loss function shows classification losses when task=classification", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "classification", algorithm: "catboost" },
      })
      // Logloss appears as both loss and metric button
      expect(screen.getAllByRole("button", { name: "Logloss" }).length).toBeGreaterThanOrEqual(1)
      expect(screen.getByRole("button", { name: "CrossEntropy" })).toBeTruthy()
      // Should NOT contain regression-only losses
      expect(screen.queryByRole("button", { name: "Poisson" })).toBeNull()
    })

    it("Tweedie variance power slider only visible when loss_function=Tweedie", () => {
      // Without Tweedie: no slider
      const { unmount } = render(
        <GraphProvider allNodes={[]} edges={[]}>
          <ModellingConfig {...defaultProps()} />
        </GraphProvider>,
      )
      expect(screen.queryByText(/Variance power/)).toBeNull()
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
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Hyperparameter JSON editor
  // ═════════════════════════════════════════════════════════════════

  describe("Hyperparameter JSON editor", () => {
    beforeEach(() => { defaultPane = "params" })
    it("renders one Hyperparameters JSON editor without dedicated parameter fields", () => {
      renderConfig()
      expect(screen.getByText("Hyperparameters")).toBeTruthy()
      expect(screen.getByLabelText("CatBoost hyperparameters JSON")).toBeTruthy()
      expect(document.querySelectorAll("textarea")).toHaveLength(1)
      expect(screen.queryAllByRole("spinbutton")).toHaveLength(0)
    })

    it("textarea shows default params when config.params is empty", () => {
      renderConfig()
      const editor = screen.getByLabelText(
        "CatBoost hyperparameters JSON",
      ) as HTMLTextAreaElement
      expect(JSON.parse(editor.value)).toEqual({
        iterations: 1000,
        learning_rate: 0.05,
        depth: 6,
        l2_leaf_reg: 3,
        early_stopping_rounds: 50,
      })
    })

    it("textarea shows custom params from config", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", params: { iterations: 500, depth: 8 } },
      })
      const textarea = screen.getByLabelText("CatBoost hyperparameters JSON") as HTMLTextAreaElement
      const parsed = JSON.parse(textarea.value)
      expect(parsed).toEqual({ iterations: 500, depth: 8 })
    })

    it("applying JSON commits arbitrary algorithm parameters", () => {
      const { props } = renderConfig()
      const textarea = screen.getByLabelText("CatBoost hyperparameters JSON")
      fireEvent.change(textarea, {
        target: {
          value: '{"grow_policy":"Lossguide","max_leaves":64,"custom":{"enabled":true}}',
        },
      })
      fireEvent.click(screen.getByRole("button", { name: "Apply" }))
      expect(props.onUpdate).toHaveBeenCalledWith("params", {
        grow_policy: "Lossguide",
        max_leaves: 64,
        custom: { enabled: true },
      })
    })

    it("invalid JSON shows error and does not commit", () => {
      const { props } = renderConfig()
      const textarea = screen.getByLabelText("CatBoost hyperparameters JSON")
      fireEvent.change(textarea, { target: { value: "{bad json" } })
      fireEvent.click(screen.getByRole("button", { name: "Apply" }))
      // Error message text varies by JS engine — just check the border uses the danger token.
      expect(screen.getByRole("alert")).toBeTruthy()
      // onUpdate should not have been called with params
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
    it("renders split strategy buttons (random, temporal, group)", () => {
      renderConfig()
      expect(screen.getByRole("button", { name: "random" })).toBeTruthy()
      expect(screen.getByRole("button", { name: "temporal" })).toBeTruthy()
      expect(screen.getByRole("button", { name: "group" })).toBeTruthy()
    })

    it("random split shows validation, holdout, and seed inputs", () => {
      renderConfig()
      expect(screen.getByText("Validation")).toBeTruthy()
      expect(screen.getByText("Holdout")).toBeTruthy()
      expect(screen.getByText("Seed")).toBeTruthy()
      expect(screen.getByDisplayValue("0.2")).toBeTruthy()
      expect(screen.getByDisplayValue("42")).toBeTruthy()
    })

    it("changing split strategy to temporal calls handleSplitUpdate", () => {
      const { props } = renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "temporal" }))
      expect(props.onUpdate).toHaveBeenCalledWith("split", expect.objectContaining({ strategy: "temporal" }))
    })

    it("temporal split shows date column and cutoff date", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "catboost",
          split: { strategy: "temporal", validation_size: 0.2, seed: 42 },
        },
      })
      expect(screen.getByText("Date column")).toBeTruthy()
      expect(screen.getByText("Cutoff date")).toBeTruthy()
    })

    it("group split shows group column and test size", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "catboost",
          split: { strategy: "group", validation_size: 0.2, seed: 42 },
        },
      })
      expect(screen.getByText("Group column")).toBeTruthy()
    })

    it("metrics checkboxes for regression render correctly", () => {
      renderConfig({ activePane: "target" })
      // Regression metrics (display labels)
      expect(screen.getByRole("button", { name: "Gini" })).toBeTruthy()
      expect(screen.getByRole("button", { name: "R²" })).toBeTruthy()
      // RMSE and MAE appear twice (loss function + metric) — check both exist
      expect(screen.getAllByRole("button", { name: "RMSE" }).length).toBeGreaterThanOrEqual(2)
      expect(screen.getAllByRole("button", { name: "MAE" }).length).toBeGreaterThanOrEqual(2)
    })

    it("clicking a metric button toggles it", () => {
      const { props } = renderConfig({
        activePane: "target",
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", metrics: ["gini", "rmse"] },
      })
      // Click "MSE" metric to add it (only appears once — not a loss function)
      fireEvent.click(screen.getByRole("button", { name: "MSE" }))
      expect(props.onUpdate).toHaveBeenCalledWith("metrics", ["gini", "rmse", "mse"])
    })

    it("clicking a selected metric removes it", () => {
      const { props } = renderConfig({
        activePane: "target",
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", metrics: ["gini", "rmse"] },
      })
      // Click "Gini" to remove it (only appears once — not a loss function)
      fireEvent.click(screen.getByRole("button", { name: "Gini" }))
      expect(props.onUpdate).toHaveBeenCalledWith("metrics", ["rmse"])
    })

    it("classification task shows classification metrics", () => {
      renderConfig({
        activePane: "target",
        config: { _nodeId: "node_1", target: "loss_ratio", task: "classification", algorithm: "catboost" },
      })
      expect(screen.getByRole("button", { name: "AUC" })).toBeTruthy()
      // Logloss appears twice (loss function + metric)
      expect(screen.getAllByRole("button", { name: "Logloss" }).length).toBeGreaterThanOrEqual(2)
      // Regression-only metrics should NOT be visible
      expect(screen.queryByRole("button", { name: "Gini" })).toBeNull()
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

    it("keeps validation hidden and Train enabled before the first press", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "", task: "regression", algorithm: "catboost" },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toBeEnabled()
      expect(screen.queryByRole("alert")).not.toBeInTheDocument()
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
      expect(banner).toHaveTextContent("Add factors or tick 'All features'")
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
      expect(screen.queryByRole("alert")).not.toBeInTheDocument()
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
      expect(screen.queryByText(/Choose a training loss/)).not.toBeInTheDocument()
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
      expect(screen.queryByText(/Choose a GLM distribution family/)).not.toBeInTheDocument()
      fireEvent.click(trainBtn)
      expect(screen.getByRole("alert")).toHaveTextContent("Choose a GLM distribution family")
    })

    it("surfaces an empty factor set only after Train is pressed (glm)", () => {
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
      expect(screen.queryByText(/Add factors or tick 'All features'/)).not.toBeInTheDocument()
      fireEvent.click(trainBtn)
      expect(screen.getByRole("alert")).toHaveTextContent("Add factors or tick 'All features'")
    })

    it("surfaces missing Tweedie variance power only after Train is pressed (glm)", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "glm",
          family: "tweedie",
          all_factors: true,
        },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toBeEnabled()
      expect(screen.queryByText(/Set the Tweedie variance power/)).not.toBeInTheDocument()
      fireEvent.click(trainBtn)
      expect(screen.getByRole("alert")).toHaveTextContent("Set the Tweedie variance power")
    })

    it("surfaces missing Neg. Binomial theta only after Train is pressed (glm)", () => {
      // RustyStats does not estimate theta — an unset value would silently
      // fit at theta=1.0, so the UI must not submit one.
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "glm",
          family: "negbinomial",
          all_factors: true,
        },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toBeEnabled()
      expect(screen.queryByText(/Set the Negative Binomial dispersion/)).not.toBeInTheDocument()
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
          all_factors: true,
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
          all_factors: true,
          regularization: "elastic_net",
        },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toBeEnabled()
      expect(screen.queryByText(/Set the elastic-net L1 ratio/)).not.toBeInTheDocument()
      fireEvent.click(trainBtn)
      expect(screen.getByRole("alert")).toHaveTextContent("Set the elastic-net L1 ratio")
    })

    it("train button enables once the objective is explicit", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "glm",
          family: "poisson",
          all_factors: true,
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
        expect(cached.result.status).toBe("ok")
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
      const config = { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost" }
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
        expect(screen.getByText("1,000,000")).toBeTruthy()
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
        expect(toasts.some((t) => t.text.includes("RAM estimate failed"))).toBe(true)
      })
      // Inline warning is shown
      expect(screen.getByText(/RAM estimate unavailable/)).toBeTruthy()
      // Verify toast content
      const toasts = useToastStore.getState().toasts
      const ramToast = toasts.find((t) => t.text.includes("RAM estimate failed"))!
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
      expect(screen.queryByText(/RAM estimate unavailable/)).toBeNull()
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
      expect(props.onUpdate).toHaveBeenCalledWith("algorithm", "glm")
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Task switching updates metrics
  // ═════════════════════════════════════════════════════════════════

  describe("Task switching metrics", () => {
    it("switching to classification sets classification metrics", () => {
      const { props } = renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "classification" }))
      expect(props.onUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ task: "classification", metrics: ["auc", "logloss"] }),
      )
    })

    it("switching back to regression sets regression metrics", () => {
      const { props } = renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "classification", algorithm: "catboost", metrics: ["auc", "logloss"] },
      })
      fireEvent.click(screen.getByRole("button", { name: "regression" }))
      expect(props.onUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ task: "regression", metrics: ["gini", "rmse"] }),
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
        metrics: ["gini", "poisson_deviance"],
      })
    })

    it("clicking Tweedie sets loss_function and objective-matched metrics", () => {
      const { props } = renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "Tweedie" }))
      expect(props.onUpdate).toHaveBeenCalledWith({
        loss_function: "Tweedie",
        metrics: ["gini", "tweedie_deviance"],
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
        metrics: ["gini", "rmse"],
      })
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Row limit input
  // ═════════════════════════════════════════════════════════════════

  describe("Row limit input", () => {
    beforeEach(() => { defaultPane = "train" })
    it("renders row limit input with placeholder", () => {
      renderConfig()
      expect(screen.getByLabelText("Row limit")).toBeTruthy()
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
      const regionSpan = screen.getAllByText("region").find(el => el.tagName === "SPAN")!
      fireEvent.click(within(regionSpan.closest("div")!).getByRole("button", { name: "Exclude" }))
      expect(props.onUpdate).toHaveBeenCalledWith({ exclude: ["age", "region"] })
    })

    it("including a column from exclude list removes only that column", () => {
      const { props } = renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", exclude: ["age", "region"] },
      })
      const regionSpan = screen.getAllByText("region").find(el => el.tagName === "SPAN")!
      fireEvent.click(within(regionSpan.closest("div")!).getByRole("button", { name: "Include" }))
      expect(props.onUpdate).toHaveBeenCalledWith({ exclude: ["age"] })
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Split strategy buttons
  // ═════════════════════════════════════════════════════════════════

  describe("Split strategy selection", () => {
    beforeEach(() => { defaultPane = "split" })
    it("clicking group split calls onUpdate with group strategy", () => {
      const { props } = renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "group" }))
      expect(props.onUpdate).toHaveBeenCalledWith("split", expect.objectContaining({ strategy: "group" }))
    })

    it("clicking random split after temporal reverts strategy", () => {
      const { props } = renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "catboost",
          split: { strategy: "temporal", validation_size: 0.2, seed: 42 },
        },
      })
      fireEvent.click(screen.getByRole("button", { name: "random" }))
      expect(props.onUpdate).toHaveBeenCalledWith("split", expect.objectContaining({ strategy: "random" }))
    })
  })

  describe("MOD-M10 exclusive-pane contract", () => {
    it("shows only the algorithm gateway for an unset algorithm and rejects unsupported algorithms", () => {
      renderConfig({ config: { _nodeId: "node_1", target: "loss_ratio" } })
      expect(screen.getByText("Select Algorithm")).toBeTruthy()
      expect(screen.queryByRole("tabpanel")).toBeNull()
      cleanup()

      renderConfig({ config: { _nodeId: "node_1", algorithm: "xgboost" } })
      expect(screen.getByRole("alert")).toHaveTextContent("Unsupported modelling algorithm: xgboost.")
    })

    it("keeps the selected algorithm immutable and renders exactly one owning pane for both algorithms", () => {
      for (const algorithm of ["catboost", "glm"] as const) {
        for (const pane of ["target", "features", "params", "split", "train"] as const) {
          const { unmount } = renderConfig({ activePane: pane, config: { _nodeId: "node_1", algorithm, target: "loss_ratio", loss_function: "RMSE" } })
          expect(screen.getByRole("tabpanel")).toHaveAttribute("id", `modelling-${pane}-pane`)
          expect(screen.queryByRole("button", { name: "CatBoost" })).toBeNull()
          expect(screen.queryByRole("button", { name: "GLM" })).toBeNull()
          unmount()
        }
      }
    })

    it("keeps dirty invalid JSON across navigation but resets the draft for a different node", () => {
      const { rerender, props } = renderConfig({ activePane: "params" })
      const json = screen.getByLabelText("CatBoost hyperparameters JSON")
      fireEvent.change(json, { target: { value: "{invalid" } })
      fireEvent.click(screen.getByRole("button", { name: "Apply" }))
      expect(screen.getByRole("alert")).toBeTruthy()
      rerender(<GraphProvider allNodes={[]} edges={[]}><ModellingConfig {...props} activePane="train" /></GraphProvider>)
      rerender(<GraphProvider allNodes={[]} edges={[]}><ModellingConfig {...props} activePane="params" /></GraphProvider>)
      expect(screen.getByLabelText("CatBoost hyperparameters JSON")).toHaveValue("{invalid")
      rerender(<GraphProvider allNodes={[]} edges={[]}><ModellingConfig {...props} activePane="params" config={{ ...props.config, _nodeId: "node_2", params: { depth: 8 } }} /></GraphProvider>)
      expect(screen.getByLabelText("CatBoost hyperparameters JSON")).toHaveValue(JSON.stringify({ depth: 8 }, null, 2))
    })

    it("merges Apply exactly, Revert restores the projection, and Train owns GPU, row limit, and MLflow", () => {
      const { rerender, props } = renderConfig({ activePane: "params", config: { _nodeId: "node_1", algorithm: "catboost", params: { depth: 6, task_type: "GPU" } } })
      fireEvent.change(screen.getByLabelText("CatBoost hyperparameters JSON"), { target: { value: '{"iterations":200,"custom":true}' } })
      fireEvent.click(screen.getByRole("button", { name: "Apply" }))
      expect(props.onUpdate).toHaveBeenCalledWith("params", {
        iterations: 200,
        custom: true,
        task_type: "GPU",
      })
      fireEvent.click(screen.getByRole("button", { name: "Revert" }))
      expect(screen.getByLabelText("CatBoost hyperparameters JSON")).toHaveValue(JSON.stringify({ depth: 6 }, null, 2))
      rerender(<GraphProvider allNodes={[]} edges={[]}><ModellingConfig {...props} activePane="train" /></GraphProvider>)
      expect(screen.getByRole("checkbox", { name: /GPU training/ })).toBeTruthy()
      expect(screen.getByLabelText("Row limit")).toBeTruthy()
      expect(screen.getByPlaceholderText("MLflow experiment")).toBeTruthy()
      expect(screen.getByPlaceholderText("MLflow model name")).toBeTruthy()
    })

    it("uses the standard themed form styling throughout the Train pane", () => {
      renderConfig({ activePane: "train" })

      const gpu = screen.getByRole("checkbox", { name: /GPU training/ })
      const rowLimit = screen.getByLabelText("Row limit")
      const experiment = screen.getByLabelText("MLflow experiment path")
      const modelName = screen.getByLabelText("MLflow model name")

      expect(gpu).toHaveClass("accent-purple-500")
      expect(rowLimit).toHaveAttribute("placeholder", "All rows")
      for (const field of [rowLimit, experiment, modelName]) {
        expect(field).toHaveStyle({
          background: "var(--bg-input)",
          color: "var(--text-primary)",
        })
        expect(field.getAttribute("style")).toContain("border: 1px solid var(--border)")
      }
      expect(rowLimit).toHaveClass("w-32", "font-mono")
      expect(experiment).toHaveClass("w-full", "rounded-lg", "font-mono")
      expect(modelName).toHaveClass("w-full", "rounded-lg", "font-mono")
    })
  })
})
