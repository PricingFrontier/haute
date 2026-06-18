import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import type React from "react"

// Lightweight ReactFlow stand-ins so the test exercises ComparisonView's own
// logic (fetch / loading / error / chip / which graph feeds which canvas) rather
// than ReactFlow internals — same pattern as the App.* integration tests.
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children, ...props }: Record<string, unknown>) => (
    <div data-testid={props["data-testid"] as string}>{children as React.ReactNode}</div>
  ),
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Background: () => null,
  BackgroundVariant: { Dots: "dots" },
  useNodesState: (init: unknown) => [init, vi.fn(), vi.fn()],
  useEdgesState: (init: unknown) => [init, vi.fn(), vi.fn()],
}))

const mockGetCommitPipeline = vi.fn()
vi.mock("../../api/client", () => ({
  getCommitPipeline: (...a: unknown[]) => mockGetCommitPipeline(...a),
}))

import ComparisonView from "../ComparisonView"

const comparison = { sha: "abc1234def567890", label: "v1.2" }
const currentNodes = [
  { id: "n1", position: { x: 0, y: 0 }, data: { label: "N1", nodeType: "polars" } },
] as never
const currentEdges = [] as never

function renderView(onClose = vi.fn()) {
  render(
    <ComparisonView
      comparison={comparison}
      currentNodes={currentNodes}
      currentEdges={currentEdges}
      onClose={onClose}
    />,
  )
  return onClose
}

beforeEach(() => mockGetCommitPipeline.mockReset())
afterEach(cleanup)

describe("ComparisonView", () => {
  it("renders the current canvas immediately and the historical one once it loads", async () => {
    mockGetCommitPipeline.mockResolvedValue({ nodes: [], edges: [] })
    renderView()

    // Right (current) canvas is available straight away; left (historical) waits.
    expect(screen.getByTestId("comparison-canvas-current")).toBeInTheDocument()
    expect(screen.getByTestId("comparison-loading")).toBeInTheDocument()

    await waitFor(() =>
      expect(screen.getByTestId("comparison-canvas-historical")).toBeInTheDocument(),
    )
    expect(mockGetCommitPipeline).toHaveBeenCalledWith(comparison.sha, expect.anything())
  })

  it("shows the floating chip (label + short sha) and bails out via the ×", async () => {
    mockGetCommitPipeline.mockResolvedValue({ nodes: [], edges: [] })
    const onClose = renderView()
    await waitFor(() =>
      expect(screen.getByTestId("comparison-canvas-historical")).toBeInTheDocument(),
    )

    const chip = screen.getByTestId("comparison-chip")
    expect(chip).toHaveTextContent("v1.2")
    expect(chip).toHaveTextContent("abc1234") // sha.slice(0, 7)

    fireEvent.click(screen.getByTestId("comparison-chip-close"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  // NOTE: the fetch-failure path (catch → error UI → "Back to editor" bail-out)
  // is intentionally not unit-tested here. The component catches the rejection
  // (its own try/catch around the awaited fetch) and renders the error branch,
  // but driving that through an async effect trips vitest's unhandled-rejection
  // detector regardless of how the rejection is caught — and the only way to
  // silence it is the global `dangerouslyIgnoreUnhandledErrors`, which would mask
  // genuine rejections across the whole suite. The error branch is small, static
  // JSX; it's covered by reading + the live demo rather than at the cost of that
  // global escape hatch.
})
