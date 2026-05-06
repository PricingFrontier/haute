import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { RegisteredModelPicker, ExperimentRunPicker } from "../MlflowModelPicker"
import type { MlflowBrowserState } from "../../../hooks/useMlflowBrowser"

afterEach(cleanup)

function makeMlflow(overrides: Partial<MlflowBrowserState> = {}): MlflowBrowserState {
  return {
    experiments: [],
    runs: [],
    models: [],
    modelVersions: [],
    modelVersionsFor: "",
    loadingModels: false,
    loadingExperiments: false,
    loadingRuns: false,
    loadingVersions: false,
    errorModels: null,
    errorExperiments: null,
    errorRuns: null,
    errorVersions: null,
    browseExpId: "",
    setBrowseExpId: vi.fn(),
    setRuns: vi.fn(),
    resetRunsGuard: vi.fn(),
    refreshModels: vi.fn(),
    refreshExperiments: vi.fn(),
    refreshRuns: vi.fn(),
    refreshVersions: vi.fn(),
    ...overrides,
  } as MlflowBrowserState
}

describe("RegisteredModelPicker", () => {
  it("renders Model Name label and select", () => {
    render(<RegisteredModelPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow()} />)
    expect(screen.getByText("Model Name")).toBeInTheDocument()
    expect(screen.getByText("Select a model...")).toBeInTheDocument()
  })

  it("shows Loading... when models are loading", () => {
    render(<RegisteredModelPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow({ loadingModels: true })} />)
    expect(screen.getByText("Loading...")).toBeInTheDocument()
  })

  it("renders model options", () => {
    const mlflow = makeMlflow({
      models: [{ name: "my-model", latest_versions: [] }],
    })
    render(<RegisteredModelPicker config={{}} onUpdate={vi.fn()} mlflow={mlflow} />)
    expect(screen.getByText("my-model")).toBeInTheDocument()
  })

  it("selecting a model calls onUpdate with model name and version=latest", () => {
    const onUpdate = vi.fn()
    const mlflow = makeMlflow({
      models: [{ name: "my-model", latest_versions: [] }],
    })
    render(<RegisteredModelPicker config={{}} onUpdate={onUpdate} mlflow={mlflow} />)
    const select = screen.getAllByRole("combobox")[0]
    fireEvent.change(select, { target: { value: "my-model" } })
    expect(onUpdate).toHaveBeenCalledWith({ registered_model: "my-model", version: "latest" })
  })

  it("shows Version select when a model is selected", () => {
    render(<RegisteredModelPicker config={{ registered_model: "my-model" }} onUpdate={vi.fn()} mlflow={makeMlflow()} />)
    expect(screen.getByText("Version")).toBeInTheDocument()
    expect(screen.getByText("latest")).toBeInTheDocument()
  })

  it("only shows versions loaded for the selected model", () => {
    const mlflow = makeMlflow({
      modelVersionsFor: "old-model",
      modelVersions: [{ version: "4", run_id: "r4", status: "READY", description: "" }],
    })
    render(<RegisteredModelPicker config={{ registered_model: "new-model" }} onUpdate={vi.fn()} mlflow={mlflow} />)
    expect(screen.queryByText(/v4/)).not.toBeInTheDocument()
  })

  it("does not show Version select when no model is selected", () => {
    render(<RegisteredModelPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow()} />)
    expect(screen.queryByText("Version")).not.toBeInTheDocument()
  })

  it("shows error message when errorModels is set", () => {
    render(<RegisteredModelPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow({ errorModels: "Network error" })} />)
    expect(screen.getByText("Network error")).toBeInTheDocument()
  })
})

describe("ExperimentRunPicker", () => {
  it("renders Experiment label", () => {
    render(<ExperimentRunPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow()} />)
    expect(screen.getByText("Experiment")).toBeInTheDocument()
  })

  it("renders experiment options", () => {
    const mlflow = makeMlflow({
      experiments: [{ experiment_id: "exp1", name: "My Experiment" }],
    })
    render(<ExperimentRunPicker config={{}} onUpdate={vi.fn()} mlflow={mlflow} />)
    expect(screen.getByText("My Experiment")).toBeInTheDocument()
  })

  it("shows Run ID text input", () => {
    render(<ExperimentRunPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow()} />)
    expect(screen.getByText("Run ID")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("e.g. a1b2c3d4e5f6...")).toBeInTheDocument()
  })

  it("shows Artifact Path when showArtifactPath is true", () => {
    render(<ExperimentRunPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow()} showArtifactPath />)
    expect(screen.getByText("Artifact Path")).toBeInTheDocument()
  })

  it("does not show Artifact Path by default", () => {
    render(<ExperimentRunPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow()} />)
    expect(screen.queryByText("Artifact Path")).not.toBeInTheDocument()
  })

  it("shows Run select when experiment is selected", () => {
    const mlflow = makeMlflow({ browseExpId: "exp1" })
    render(<ExperimentRunPicker config={{}} onUpdate={vi.fn()} mlflow={mlflow} />)
    expect(screen.getByText("Run")).toBeInTheDocument()
  })
})
