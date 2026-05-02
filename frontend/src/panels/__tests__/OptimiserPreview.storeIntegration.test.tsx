import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import OptimiserPreview, { type SolveResult } from "../OptimiserPreview"
import useNodeResultsStore, {
  resetNodeResultsDerivedCaches,
} from "../../stores/useNodeResultsStore"
import useGraphStore from "../../stores/useGraphStore"

vi.mock("../../api/client", () => ({
  applyOptimiser: vi.fn(),
  saveOptimiser: vi.fn(),
  logOptimiserToMlflow: vi.fn(),
}))

vi.mock("../../hooks/useDragResize", () => ({
  useDragResize: () => ({
    height: 320,
    containerRef: { current: null },
    onDragStart: vi.fn(),
  }),
}))

vi.mock("../../stores/useSettingsStore", () => ({
  default: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      mlflow: { status: "connected", backend: "local", host: "" },
    }),
}))

function resetStore() {
  resetNodeResultsDerivedCaches()
  useGraphStore.setState({ structuralVersion: 0 })
  useNodeResultsStore.setState({
    previews: {},
    pinnedPreviewNodeId: null,
    columnCache: {},
    solveResults: {},
    solveJobs: {},
    trainResults: {},
    trainJobs: {},
  })
}

function makeSolveResult(overrides: Partial<SolveResult> = {}): SolveResult {
  return {
    total_objective: 100,
    baseline_objective: 80,
    constraints: { loss_ratio: 0.6 },
    baseline_constraints: { loss_ratio: 0.55 },
    lambdas: { loss_ratio: 0.1 },
    converged: true,
    ...overrides,
  }
}

describe("OptimiserPreview store integration", () => {
  beforeEach(() => {
    resetStore()
  })

  afterEach(() => {
    cleanup()
    resetStore()
  })

  it("re-renders from the result store when a frontier point is clicked", async () => {
    const store = useNodeResultsStore.getState()
    store.startSolveJob("opt_1", "job_123", "My Optimiser", { loss_ratio: { max: 1.05 } }, "h1")
    store.completeSolveJob(
      "opt_1",
      makeSolveResult({
        frontier: {
          status: "ok",
          points: Array.from({ length: 5 }, (_, i) => ({
            total_objective: 120 + i,
            loss_ratio: 0.55 + i * 0.02,
            lambda_loss_ratio: 0.01 + i * 0.01,
            converged: true,
          })),
          n_points: 5,
          points_returned: 5,
          constraint_names: ["loss_ratio"],
          points_limit: 2000,
          points_truncated: false,
        },
      }),
    )

    const data = useNodeResultsStore.getState().getOptimiserPreview("opt_1")
    expect(data).not.toBeNull()
    render(<OptimiserPreview data={data!} nodeId="opt_1" />)

    fireEvent.click(screen.getByRole("button", { name: "Select frontier point 3" }))

    expect(await screen.findByText("Point 3 of 5")).toBeInTheDocument()
    expect(useNodeResultsStore.getState().solveResults.opt_1.selectedPointIndex).toBe(2)
  })
})
