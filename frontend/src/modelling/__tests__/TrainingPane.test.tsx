import { useState } from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { makeTrainResult } from "../../test-utils/factories"
import { TrainingPane } from "../TrainingPane"
import { stageConfiguration } from "../stageSettings"

const props = () => ({
  settings: stageConfiguration({}).stages, gbm: <div>Tree settings</div>,
  stages: ["full", "pruned"] as const, pathAvailable: true, pathChanged: false,
  active: null, busy: false, blocked: false, onRun: vi.fn(), onCancel: vi.fn(),
  onUpdate: vi.fn(), onApply: vi.fn(), onDiscard: vi.fn(), preview: false,
})

afterEach(cleanup)

it("labels the selected applied point as Applied instead of offering it again", () => {
  const p = props()
  const current = makeTrainResult({ staged: { schema_version: 1, selected_stage: "smoothed",
    diagnostics_basis: "partition", reference_fit: "development", configuration: {},
    evaluation_identity: "a".repeat(64), fit_config_digest: "b".repeat(64),
    stages: { smoothed: { revision: "same-candidate" } }, pruning_path: null,
    path_unavailable_reason: null, recommendation: null } })
  const path = { stage: "smoothed", parent: "banded", points: [{ id: "point-1", revision: "same-candidate", identity: false, status: "changed" }] } as unknown as NonNullable<Parameters<typeof TrainingPane>[0]["candidatePath"]>
  const { rerender } = render(<TrainingPane {...p} currentResult={current} preview previewStage="smoothed"
    candidatePath={path} candidatePoint="point-1" stages={["full", "pruned", "banded", "smoothed"]} />)
  expect(screen.getByText("Applied", { exact: true })).toBeInTheDocument()
  expect(screen.queryByText("Candidate ready", { exact: true })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "Apply smoothing" })).not.toBeInTheDocument()
  rerender(<TrainingPane {...p} currentResult={current} preview previewStage="smoothed"
    candidatePath={{ ...path, points: [{ ...path.points[0], revision: "different" }] }} candidatePoint="point-1"
    stages={["full", "pruned", "banded", "smoothed"]} />)
  expect(screen.getByRole("button", { name: "Apply smoothing" })).toBeEnabled()
})

it("keeps an unchanged point actionable until its phase is explicitly skipped", () => {
  const p = props()
  const path = { id: "path", stage: "pruned", parent: "full", selected_point: "unchanged", points: [{ id: "unchanged", identity: true, revision: "parent", status: "unchanged" }] } as unknown as NonNullable<Parameters<typeof TrainingPane>[0]["candidatePath"]>
  const current = makeTrainResult({ staged: { schema_version: 1, selected_stage: "full", diagnostics_basis: "partition", reference_fit: "development", configuration: { enabled: { pruned: true } }, evaluation_identity: "a".repeat(64), fit_config_digest: "b".repeat(64), stages: { full: { revision: "parent" } }, pruning_path: null, path_unavailable_reason: null, recommendation: null } })
  const { rerender } = render(<TrainingPane {...p} currentResult={current} preview candidatePath={path} candidatePoint="unchanged" stages={["full"]} />)
  fireEvent.click(screen.getByRole("button", { name: "Keep unchanged" }))
  expect(p.onApply).toHaveBeenCalledOnce()
  const applied = { ...current, staged: { ...current.staged!, configuration: { enabled: { pruned: false } }, paths: { pruned: path } } }
  rerender(<TrainingPane {...p} currentResult={applied} preview candidatePath={path} candidatePoint="unchanged" stages={["full"]} />)
  expect(screen.queryByRole("button", { name: "Keep unchanged" })).not.toBeInTheDocument()
})

describe("Training pane", () => {
  it("keeps independent actions visible when cards are collapsed and checks their parent", () => {
    const p = props()
    render(<TrainingPane {...p} />)
    expect(screen.queryByRole("button", { name: "GBM settings" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Train GBM" }))
    expect(p.onRun).toHaveBeenCalledWith("full")
    fireEvent.click(screen.getByRole("button", { name: "Build pruning" }))
    expect(p.onRun).toHaveBeenCalledWith("pruned")
    expect(screen.getByRole("button", { name: "Build banding" })).toBeEnabled()
    expect(screen.getByRole("button", { name: "Build smoothing" })).toBeDisabled()
  })
  it("blocks actions while a stage field has an invalid uncommitted draft", () => {
    const p = props()
    const { rerender } = render(<TrainingPane {...p} view="params" />)
    expect(screen.queryByRole("button", { name: "Train GBM" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Pruning settings" }))
    fireEvent.change(screen.getByRole("spinbutton", { name: "Path points" }), { target: { value: "1" } })
    rerender(<TrainingPane {...p} view="train" />)
    expect(screen.getByRole("button", { name: "Build pruning" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Build pruning" }))
    expect(p.onRun).not.toHaveBeenCalled()
    rerender(<TrainingPane {...p} view="params" />)
    expect(screen.getByRole("spinbutton", { name: "Path points" })).toHaveValue(1)
    fireEvent.change(screen.getByRole("spinbutton", { name: "Path points" }), { target: { value: "16" } })
    fireEvent.blur(screen.getByRole("spinbutton", { name: "Path points" }))
    expect(p.onUpdate).toHaveBeenCalledWith(expect.objectContaining({ path: expect.objectContaining({ steps: 16 }) }))
    rerender(<TrainingPane {...p} view="train" />)
    expect(screen.getByRole("button", { name: "Build pruning" })).toBeEnabled()
  })
  it("offers Cancel only on the running stage, even when its settings are closed", () => {
    const p = props()
    render(<TrainingPane {...p} active="pruned" blocked />)
    expect(screen.getByRole("button", { name: "Train GBM" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
    expect(p.onCancel).toHaveBeenCalledOnce()
    expect(screen.queryByRole("button", { name: "Build pruning" })).not.toBeInTheDocument()
  })
  it("keeps Apply and Discard separate when a candidate is ready", () => {
    const p = props()
    render(<TrainingPane {...p} preview pathAvailable={false} pathChanged />)
    expect(screen.getByRole("button", { name: "Apply pruning" })).toBeEnabled()
    expect(screen.queryByRole("button", { name: "Pruning settings" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Apply pruning" }))
    fireEvent.click(screen.getByRole("button", { name: "Clear prepared path" }))
    expect(p.onApply).toHaveBeenCalledOnce()
    expect(p.onDiscard).toHaveBeenCalledOnce()
  })
  it("finalises the applied output independently of previews and exposes cancellation while collapsed", () => {
    const p = props()
    const { rerender } = render(<TrainingPane {...p} preview />)
    expect(screen.getByRole("button", { name: "Finalise applied model" })).toBeEnabled()
    rerender(<TrainingPane {...p} />)
    fireEvent.click(screen.getByRole("button", { name: "Finalise applied model" }))
    expect(p.onRun).toHaveBeenCalledWith("finalise")
    rerender(<TrainingPane {...p} active="finalise" />)
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
    expect(p.onCancel).toHaveBeenCalledOnce()
  })
  it("requires a pending parent to be rebuilt before using its descendants", () => {
    render(<TrainingPane {...props()} pending={["pruned"]} />)
    expect(screen.getByRole("button", { name: "Build pruning" })).toBeEnabled()
    expect(screen.getByRole("button", { name: "Build banding" })).toBeDisabled()
    expect(screen.getByText(/settings changed. Run that stage/)).toBeVisible()
  })

})

it("skips disabled phases, retains their settings, and disables their actions", () => {
  const p = props()
  function Harness() {
    const [settings, onUpdate] = useState(p.settings)
    const [view, setView] = useState<"params" | "train">("params")
    return <><button onClick={() => setView(view === "params" ? "train" : "params")}>Switch pane</button>
      <TrainingPane {...p} stages={["full"]} view={view} settings={settings} onUpdate={onUpdate} /></>
  }
  render(<Harness />)
  expect(screen.queryByRole("combobox", { name: "Starting model" })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "Pruning settings" }))
  fireEvent.change(screen.getByRole("spinbutton", { name: "Path points" }), { target: { value: "1" } })
  fireEvent.click(screen.getByRole("checkbox", { name: "Enable Pruning" }))
  fireEvent.click(screen.getByRole("button", { name: "Banding settings" }))
  expect(screen.getByText("Uses Full GBM")).toBeVisible()
  fireEvent.click(screen.getByRole("checkbox", { name: "Enable Banding" }))
  fireEvent.click(screen.getByRole("button", { name: "Smoothing settings" }))
  expect(screen.getAllByText("Uses Full GBM")).toHaveLength(2)
  fireEvent.click(screen.getByRole("button", { name: "Switch pane" }))
  expect(screen.getByRole("button", { name: "Train GBM" })).toBeEnabled()
  expect(screen.getByRole("button", { name: "Build pruning" })).toBeDisabled()
  expect(screen.getByRole("button", { name: "Build banding" })).toBeDisabled()
  expect(screen.getByRole("button", { name: "Build smoothing" })).toBeEnabled()
  fireEvent.click(screen.getByRole("button", { name: "Build smoothing" }))
  expect(p.onRun).toHaveBeenCalledWith("smoothed")
  fireEvent.click(screen.getByRole("button", { name: "Switch pane" }))
  expect(screen.getByRole("spinbutton", { name: "Band tolerance" })).toHaveValue(.75)
  expect(screen.getByRole("spinbutton", { name: "Band tolerance" })).toBeDisabled()
  fireEvent.click(screen.getByRole("checkbox", { name: "Enable Pruning" }))
  expect(screen.getAllByText("Uses Pruned")).toHaveLength(2)
  expect(screen.getByRole("spinbutton", { name: "Path points" })).toHaveValue(1)
  fireEvent.click(screen.getByRole("button", { name: "Switch pane" }))
  expect(screen.getByRole("button", { name: "Build smoothing" })).toBeDisabled()
  expect(screen.getByRole("button", { name: "Train GBM" })).toBeDisabled()
})

it("does not tell users to run phases they have disabled", () => {
  const settings = stageConfiguration({ stages: { enabled: { pruned: false, banded: false, smoothed: false } } }).stages
  render(<TrainingPane {...props()} settings={settings} pending={["pruned", "banded", "smoothed"]} />)
  expect(screen.queryByText(/settings changed. Run that stage/)).not.toBeInTheDocument()
})

it("requires an intentional development-only choice before the first fit", () => {
  const p = props()
  render(<TrainingPane {...p} stages={[]} evaluationMode="Development" evaluationKey="none" />)
  expect(screen.getByRole("button", { name: "Train GBM" })).toBeDisabled()
  fireEvent.click(screen.getByRole("checkbox", { name: "Use development rows for exploration" }))
  fireEvent.click(screen.getByRole("button", { name: "Train GBM" }))
  expect(p.onRun).toHaveBeenCalledWith("full")
})

it("puts candidate keep/discard controls on Banding and retains the applied output", () => {
  const p = props()
  render(<TrainingPane {...p} preview previewStage="banded" />)
  expect(screen.queryByRole("button", { name: "Apply pruning" })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "Apply banding" }))
  expect(p.onApply).toHaveBeenCalledOnce()
  fireEvent.click(screen.getByRole("button", { name: "Clear prepared path" }))
  expect(p.onDiscard).toHaveBeenCalledOnce()
  expect(screen.getByRole("button", { name: "Build smoothing" })).toBeDisabled()
})


it("allows rebuilding saved development evidence without repeating the evaluation choice", () => {
  const p = props()
  render(<TrainingPane {...p} currentResult={makeTrainResult({ diagnostics_set: "development" })}
    evaluationMode="Development" evaluationKey="changed-params" postfitBlocked />)
  expect(screen.getByRole("button", { name: "Train GBM" })).toBeEnabled()
  expect(screen.queryByRole("checkbox", { name: "Use development rows for exploration" })).not.toBeInTheDocument()
  expect(screen.getByText("Rebuild needed")).toBeVisible()
  expect(screen.getByRole("button", { name: "Build pruning" })).toBeDisabled()
  fireEvent.click(screen.getByRole("button", { name: "Train GBM" }))
  expect(p.onRun).toHaveBeenCalledWith("full")
})

it("explains a required evaluation choice when moving from validation to development", () => {
  const p = props()
  render(<TrainingPane {...p} currentResult={makeTrainResult({ diagnostics_set: "validation" })}
    evaluationMode="Development" evaluationKey="no-validation" postfitBlocked />)
  expect(screen.getByText("Choose evaluation")).toBeVisible()
  expect(screen.getByText(/To enable Train GBM/)).toBeVisible()
  expect(screen.getByRole("button", { name: "Train GBM" })).toBeDisabled()
  fireEvent.click(screen.getByRole("checkbox", { name: "Use development rows for exploration" }))
  expect(screen.getByRole("button", { name: "Train GBM" })).toBeEnabled()
  expect(screen.getByText("Rebuild needed")).toBeVisible()
})

it("uses Apply as the primary action when the previous phase prepared its candidate", () => {
  const p = props()
  render(<TrainingPane {...p} preview />)
  expect(screen.queryByRole("button", { name: "Build pruning" })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "Apply pruning" }))
  expect(p.onApply).toHaveBeenCalledOnce()
  expect(screen.queryByText(/Automatically prepares/)).not.toBeInTheDocument()
})

it("refreshes a candidate whose pruning settings changed before allowing Apply", () => {
  const p = props()
  const candidate = makeTrainResult({ staged: { schema_version: 1, selected_stage: "pruned", diagnostics_basis: "partition", reference_fit: "validation-0",
    configuration: { path: p.settings.path }, evaluation_identity: "a".repeat(64), fit_config_digest: "b".repeat(64),
    stages: { full: {}, pruned: { parent: "full", settings: { ...p.settings.pruning, max_tables: 3 } } },
    pruning_path: null, path_unavailable_reason: null, recommendation: null } })
  render(<TrainingPane {...p} preview previewResult={candidate} />)
  expect(screen.queryByRole("button", { name: "Apply pruning" })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "Build pruning" }))
  expect(p.onRun).toHaveBeenCalledWith("pruned")
  expect(p.onApply).not.toHaveBeenCalled()
})

it("requires a changed parent to be rebuilt before applying its prepared descendant", () => {
  render(<TrainingPane {...props()} preview previewStage="banded" pending={["pruned"]} />)
  expect(screen.queryByRole("button", { name: "Apply banding" })).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Build banding" })).toBeDisabled()
  expect(screen.getByRole("button", { name: "Clear prepared path" })).toBeEnabled()
})


it("keeps rebuild actions available when another stage has a ready candidate", () => {
  const p = props()
  render(<TrainingPane {...p} stages={["full", "pruned", "banded"]} preview previewStage="smoothed" />)
  for (const name of ["Train GBM", "Build pruning", "Build banding", "Finalise applied model"]) {
    expect(screen.getByRole("button", { name })).toBeEnabled()
    fireEvent.click(screen.getByRole("button", { name }))
  }
  expect(p.onRun.mock.calls.map(([stage]) => stage)).toEqual(["full", "pruned", "banded", "finalise"])
})

it("shows genuine training blockers below all stage controls", () => {
  render(<TrainingPane {...props()} stages={[]} blocked blockingReasons={["Choose a target in Model setup."]} />)
  const reasons = screen.getByTestId("training-blockers")
  expect(reasons).toHaveTextContent("Choose a target in Model setup.")
  expect(reasons).toHaveTextContent("Pruning: build and apply Full GBM first.")
  expect(screen.getByRole("button", { name: "Finalise applied model" }).compareDocumentPosition(reasons) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

it("does not let an invalid banding draft disable the GBM or pruning", () => {
  const p = props()
  const { rerender } = render(<TrainingPane {...p} view="params" />)
  fireEvent.click(screen.getByRole("button", { name: "Banding settings" }))
  fireEvent.change(screen.getByRole("spinbutton", { name: "Band tolerance" }), { target: { value: "0" } })
  rerender(<TrainingPane {...p} view="train" />)
  expect(screen.getByRole("button", { name: "Train GBM" })).toBeEnabled()
  expect(screen.getByRole("button", { name: "Build pruning" })).toBeEnabled()
  expect(screen.getByRole("button", { name: "Build banding" })).toBeDisabled()
  expect(screen.getByTestId("training-blockers")).toHaveTextContent("Band tolerance")
})
