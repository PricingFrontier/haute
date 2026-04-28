import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"

// ---------------------------------------------------------------------------
// Mocks — must be declared before importing the component under test
// ---------------------------------------------------------------------------

const mockMlflow = {
  experiments: [] as { experiment_id: string; name: string }[],
  runs: [] as { run_id: string; run_name: string; metrics: Record<string, number>; artifacts: string[] }[],
  models: [] as { name: string; latest_versions: { version: string; status: string; run_id: string }[] }[],
  modelVersions: [] as { version: string; run_id: string; status: string; description: string }[],
  loadingExperiments: false,
  loadingRuns: false,
  loadingModels: false,
  loadingVersions: false,
  errorExperiments: "",
  errorRuns: "",
  errorModels: "",
  errorVersions: "",
  browseExpId: "",
  setBrowseExpId: vi.fn(),
  setRuns: vi.fn(),
  refreshExperiments: vi.fn(),
  refreshRuns: vi.fn(),
  refreshModels: vi.fn(),
  refreshVersions: vi.fn(),
  resetRunsGuard: vi.fn(),
}

vi.mock("../../../hooks/useMlflowBrowser", () => ({
  useMlflowBrowser: vi.fn(() => mockMlflow),
}))

vi.mock("../../../utils/configField", () => ({
  configField: (config: Record<string, unknown>, key: string, defaultVal: unknown) =>
    config[key] !== undefined ? config[key] : defaultVal,
}))

vi.mock("../_shared", async () => {
  const actual = await vi.importActual("../_shared")
  return {
    ...actual,
    InputSourcesBar: ({ inputSources }: { inputSources: unknown[] }) => (
      <div data-testid="input-sources">{inputSources.length}</div>
    ),
    MlflowStatusBadge: () => <div data-testid="mlflow-badge" />,
  }
})

vi.mock("../CodeEditor", () => ({
  CodeEditor: ({
    defaultValue,
    onChange,
  }: {
    defaultValue: string
    onChange: (v: string) => void
  }) => (
    <textarea
      data-testid="code-editor"
      defaultValue={defaultValue}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}))

import ModelScoreEditor from "../ModelScoreEditor"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const defaultProps = () => ({
  config: {} as Record<string, unknown>,
  onUpdate: vi.fn(),
  inputSources: [] as { varName: string; sourceLabel: string; edgeId: string }[],
  accentColor: "#8b5cf6",
})

function resetMlflow() {
  mockMlflow.experiments = []
  mockMlflow.runs = []
  mockMlflow.models = []
  mockMlflow.modelVersions = []
  mockMlflow.loadingExperiments = false
  mockMlflow.loadingRuns = false
  mockMlflow.loadingModels = false
  mockMlflow.loadingVersions = false
  mockMlflow.errorExperiments = ""
  mockMlflow.errorRuns = ""
  mockMlflow.errorModels = ""
  mockMlflow.errorVersions = ""
  mockMlflow.browseExpId = ""
  mockMlflow.setBrowseExpId.mockClear()
  mockMlflow.setRuns.mockClear()
  mockMlflow.refreshExperiments.mockClear()
  mockMlflow.refreshRuns.mockClear()
  mockMlflow.refreshModels.mockClear()
  mockMlflow.refreshVersions.mockClear()
  mockMlflow.resetRunsGuard.mockClear()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ModelScoreEditor", () => {
  beforeEach(resetMlflow)
  afterEach(cleanup)

  // 1. Renders with default registered source type
  it("renders with default registered source type selected", () => {
    render(<ModelScoreEditor {...defaultProps()} />)
    const registeredBtn = screen.getByText("Registered Model")
    const runBtn = screen.getByText("Experiment Run")
    // Active button should have a visually distinct border from inactive button
    expect(registeredBtn.style.border).not.toBe(runBtn.style.border)
  })

  // 2. Source type toggle switches between registered and run
  it("calls onUpdate when toggling source type to run", () => {
    const { onUpdate } = defaultProps()
    render(<ModelScoreEditor config={{}} onUpdate={onUpdate} inputSources={[]} accentColor="#8b5cf6" />)
    fireEvent.click(screen.getByText("Experiment Run"))
    expect(onUpdate).toHaveBeenCalledWith("sourceType", "run")
  })

  // 3. Registered mode: shows model name dropdown
  it("shows model name dropdown in registered mode", () => {
    render(<ModelScoreEditor {...defaultProps()} />)
    expect(screen.getByText("Model Name")).toBeInTheDocument()
    expect(screen.getByText("Select a model...")).toBeInTheDocument()
  })

  // 4. Registered mode: selecting a model calls onUpdate with registered_model and version
  it("selecting a model calls onUpdate with registered_model and version", () => {
    mockMlflow.models = [
      { name: "my-model", latest_versions: [{ version: "1", status: "READY", run_id: "abc" }] },
    ]
    const props = defaultProps()
    render(<ModelScoreEditor {...props} />)
    const modelSelect = screen.getByDisplayValue("Select a model...")
    fireEvent.change(modelSelect, { target: { value: "my-model" } })
    expect(props.onUpdate).toHaveBeenCalledWith({ registered_model: "my-model", version: "latest" })
  })

  // 5. Registered mode: shows version dropdown when model is selected
  it("shows version dropdown when a model is selected", () => {
    const props = defaultProps()
    props.config = { registered_model: "my-model" }
    mockMlflow.modelVersions = [
      { version: "1", run_id: "r1", status: "READY", description: "first" },
      { version: "2", run_id: "r2", status: "READY", description: "" },
    ]
    render(<ModelScoreEditor {...props} />)
    expect(screen.getByText("Version")).toBeInTheDocument()
    expect(screen.getByText("latest")).toBeInTheDocument()
    expect(screen.getByText(/v1 — READY \(first\)/)).toBeInTheDocument()
    expect(screen.getByText(/v2 — READY/)).toBeInTheDocument()
  })

  // 6. Registered mode: version change calls onUpdate
  it("version change calls onUpdate", () => {
    mockMlflow.modelVersions = [
      { version: "3", run_id: "r3", status: "READY", description: "" },
    ]
    const props = defaultProps()
    props.config = { registered_model: "my-model", version: "latest" }
    render(<ModelScoreEditor {...props} />)
    const versionSelect = screen.getByDisplayValue("latest")
    fireEvent.change(versionSelect, { target: { value: "3" } })
    expect(props.onUpdate).toHaveBeenCalledWith("version", "3")
  })

  // 7. Run mode: shows experiment dropdown
  it("shows experiment dropdown in run mode", () => {
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    expect(screen.getByText("Experiment")).toBeInTheDocument()
    expect(screen.getByText("Select an experiment...")).toBeInTheDocument()
  })

  // 8. Run mode: experiment change calls onUpdate with experiment_id and name
  it("experiment change calls onUpdate with experiment_id and experiment_name", () => {
    mockMlflow.experiments = [
      { experiment_id: "exp-1", name: "My Experiment" },
    ]
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    const expSelect = screen.getByDisplayValue("Select an experiment...")
    fireEvent.change(expSelect, { target: { value: "exp-1" } })
    expect(mockMlflow.setBrowseExpId).toHaveBeenCalledWith("exp-1")
    expect(props.onUpdate).toHaveBeenCalledWith({
      experiment_id: "exp-1",
      experiment_name: "My Experiment",
    })
    expect(mockMlflow.setRuns).toHaveBeenCalledWith([])
    expect(mockMlflow.resetRunsGuard).toHaveBeenCalled()
    expect(mockMlflow.refreshRuns).toHaveBeenCalledWith("exp-1")
  })

  // 9. Run mode: shows Run ID and Artifact Path text inputs
  it("shows Run ID and Artifact Path text inputs in run mode", () => {
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    expect(screen.getByText("Run ID")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("e.g. a1b2c3d4e5f6...")).toBeInTheDocument()
    expect(screen.getByText("Artifact Path")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("e.g. model.cbm")).toBeInTheDocument()
  })

  // 10. Run mode: Run ID text input calls onUpdate
  it("Run ID text input calls onUpdate", () => {
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    const runIdInput = screen.getByPlaceholderText("e.g. a1b2c3d4e5f6...")
    fireEvent.change(runIdInput, { target: { value: "abc123" } })
    expect(props.onUpdate).toHaveBeenCalledWith("run_id", "abc123")
  })

  // 11. Task toggle between regression and classification
  it("toggles task between regression and classification", () => {
    const props = defaultProps()
    render(<ModelScoreEditor {...props} />)
    // Default is regression
    const taskSelect = screen.getByDisplayValue("Regression")
    fireEvent.change(taskSelect, { target: { value: "classification" } })
    expect(props.onUpdate).toHaveBeenCalledWith("task", "classification")
  })

  // 12. Classification task shows proba column note
  it("shows proba column note when task is classification", () => {
    const props = defaultProps()
    props.config = { task: "classification", output_column: "score" }
    render(<ModelScoreEditor {...props} />)
    expect(screen.getByText("score_proba")).toBeInTheDocument()
    expect(screen.getByText(/Classification models also generate a/)).toBeInTheDocument()
  })

  // 13. Output column input calls onUpdate
  it("output column input calls onUpdate", () => {
    const props = defaultProps()
    render(<ModelScoreEditor {...props} />)
    const colInput = screen.getByDisplayValue("prediction")
    fireEvent.change(colInput, { target: { value: "my_score" } })
    expect(props.onUpdate).toHaveBeenCalledWith("output_column", "my_score")
  })

  // 14. Post-processing code section collapsed by default
  it("post-processing code section is collapsed by default", () => {
    render(<ModelScoreEditor {...defaultProps()} />)
    expect(screen.getByText("Post-processing Code (optional)")).toBeInTheDocument()
    expect(screen.queryByTestId("code-editor")).not.toBeInTheDocument()
  })

  // 15. Code section expanded when config has code
  it("code section is expanded when config has existing code", () => {
    const props = defaultProps()
    props.config = { code: "df = df.head(10)" }
    render(<ModelScoreEditor {...props} />)
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor).toBeInTheDocument()
    expect(editor.defaultValue).toBe("df = df.head(10)")
  })

  // 16. Code toggle expands/collapses
  it("toggling code section expands and collapses", () => {
    render(<ModelScoreEditor {...defaultProps()} />)
    // Initially collapsed
    expect(screen.queryByTestId("code-editor")).not.toBeInTheDocument()
    // Expand
    fireEvent.click(screen.getByText("Post-processing Code (optional)"))
    expect(screen.getByTestId("code-editor")).toBeInTheDocument()
    // Collapse
    fireEvent.click(screen.getByText("Post-processing Code (optional)"))
    expect(screen.queryByTestId("code-editor")).not.toBeInTheDocument()
  })

  it("does not synthesize scoring scaffold in the post-processing editor", () => {
    render(<ModelScoreEditor {...defaultProps()} />)
    fireEvent.click(screen.getByText("Post-processing Code (optional)"))
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("")
    expect(editor.defaultValue).not.toContain("score_from_config")
  })

  it("shows only configured post-processing code", () => {
    const props = defaultProps()
    props.config = { code: "df = df.with_columns(double_score=pl.col('prediction') * 2)" }
    render(<ModelScoreEditor {...props} />)
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("df = df.with_columns(double_score=pl.col('prediction') * 2)")
    expect(editor.defaultValue).not.toContain("score_from_config")
  })

  // 17. Shows error messages when MLflow hooks have errors
  it("shows error messages when MLflow hooks have errors", () => {
    mockMlflow.errorModels = "Models endpoint unavailable"
    render(<ModelScoreEditor {...defaultProps()} />)
    expect(screen.getByText("Models endpoint unavailable")).toBeInTheDocument()
  })

  it("shows version error when model is selected and errorVersions is set", () => {
    mockMlflow.errorVersions = "Versions fetch failed"
    const props = defaultProps()
    props.config = { registered_model: "my-model" }
    render(<ModelScoreEditor {...props} />)
    expect(screen.getByText("Versions fetch failed")).toBeInTheDocument()
  })

  it("shows experiment error in run mode", () => {
    mockMlflow.errorExperiments = "Experiment list unavailable"
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    expect(screen.getByText("Experiment list unavailable")).toBeInTheDocument()
  })

  it("shows run error in run mode when experiment is selected", () => {
    mockMlflow.errorRuns = "Runs fetch failed"
    mockMlflow.browseExpId = "exp-1"
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    expect(screen.getByText("Runs fetch failed")).toBeInTheDocument()
  })

  // 18. InputSourcesBar renders with input sources
  it("renders InputSourcesBar with input sources", () => {
    const props = defaultProps()
    props.inputSources = [
      { varName: "df", sourceLabel: "data_source", edgeId: "e1" },
      { varName: "df2", sourceLabel: "other_source", edgeId: "e2" },
    ]
    render(<ModelScoreEditor {...props} />)
    const bar = screen.getByTestId("input-sources")
    expect(bar).toBeInTheDocument()
    expect(bar.textContent).toBe("2")
  })

  // Additional edge cases

  it("does not show proba note in regression mode", () => {
    const props = defaultProps()
    props.config = { task: "regression" }
    render(<ModelScoreEditor {...props} />)
    expect(screen.queryByText(/also generate a/)).not.toBeInTheDocument()
  })

  it("does not show version dropdown when no model is selected", () => {
    render(<ModelScoreEditor {...defaultProps()} />)
    // "Version" label should not appear when no model selected
    expect(screen.queryByText("Version")).not.toBeInTheDocument()
  })

  it("shows MlflowStatusBadge", () => {
    render(<ModelScoreEditor {...defaultProps()} />)
    expect(screen.getByTestId("mlflow-badge")).toBeInTheDocument()
  })

  it("shows loading text in model dropdown when loadingModels is true", () => {
    mockMlflow.loadingModels = true
    render(<ModelScoreEditor {...defaultProps()} />)
    expect(screen.getByText("Loading...")).toBeInTheDocument()
  })

  it("artifact path input calls onUpdate in run mode", () => {
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    const artifactInput = screen.getByPlaceholderText("e.g. model.cbm")
    fireEvent.change(artifactInput, { target: { value: "model/best.cbm" } })
    expect(props.onUpdate).toHaveBeenCalledWith("artifact_path", "model/best.cbm")
  })

  it("run dropdown change calls onUpdate with run_id, run_name, and artifact_path", () => {
    mockMlflow.browseExpId = "exp-1"
    mockMlflow.runs = [
      { run_id: "run-abc", run_name: "best-run", metrics: { rmse: 0.123 }, artifacts: ["model.cbm"] },
    ]
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    const runSelect = screen.getByDisplayValue("Select a run...")
    fireEvent.change(runSelect, { target: { value: "run-abc" } })
    expect(props.onUpdate).toHaveBeenCalledWith({
      run_id: "run-abc",
      run_name: "best-run",
      artifact_path: "model.cbm",
    })
  })

  it("persisted model shows as option even when not in models list", () => {
    const props = defaultProps()
    props.config = { registered_model: "legacy-model" }
    mockMlflow.models = [] // model not in the fetched list
    render(<ModelScoreEditor {...props} />)
    // Should show the persisted model as a fallback option
    const options = screen.getAllByRole("option")
    const values = options.map((o) => o.getAttribute("value"))
    expect(values).toContain("legacy-model")
  })
})
