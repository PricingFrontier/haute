import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react"
import type React from "react"

// Lightweight ReactFlow stand-in so the test exercises ComparisonView's own logic
// (fetch / loading / which graph feeds which canvas / diff classing / node-click
// selection) rather than ReactFlow internals. Each node is rendered as a div
// carrying its className; clicking it invokes onNodeClick(event, node).
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({
    children,
    nodes,
    onNodeClick,
    ...props
  }: {
    children?: React.ReactNode
    nodes?: Array<{ id: string; className?: string }>
    onNodeClick?: (e: unknown, n: { id: string }) => void
  } & Record<string, unknown>) => (
    <div data-testid={props["data-testid"] as string} className={props.className as string}>
      {(nodes ?? []).map((n) => (
        <div
          key={n.id}
          data-testid={`cmp-node-${n.id}`}
          className={n.className}
          onClick={() => onNodeClick?.(null, n)}
        />
      ))}
      {children as React.ReactNode}
    </div>
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

function node(id: string, config: unknown = {}, position = { x: 0, y: 0 }) {
  return { id, position, data: { label: id, nodeType: "polars", config } }
}

function renderView(
  props: Partial<React.ComponentProps<typeof ComparisonView>> = {},
) {
  const onClose = props.onClose ?? vi.fn()
  const onSelectNode = props.onSelectNode ?? vi.fn()
  render(
    <ComparisonView
      comparison={comparison}
      currentNodes={(props.currentNodes ?? ([] as never))}
      currentEdges={[] as never}
      onClose={onClose}
      onSelectNode={onSelectNode}
    />,
  )
  return { onClose, onSelectNode }
}

beforeEach(() => mockGetCommitPipeline.mockReset())
afterEach(cleanup)

describe("ComparisonView", () => {
  it("shows a loading state, then both canvases once the historical version loads", async () => {
    mockGetCommitPipeline.mockResolvedValue({ nodes: [], edges: [] })
    renderView()

    expect(screen.getByTestId("comparison-loading")).toBeInTheDocument()

    await waitFor(() =>
      expect(screen.getByTestId("comparison-canvas-historical")).toBeInTheDocument(),
    )
    expect(screen.getByTestId("comparison-canvas-current")).toBeInTheDocument()
    expect(mockGetCommitPipeline).toHaveBeenCalledWith(comparison.sha, expect.anything())
  })

  it("shows the floating chip (label + short sha) and bails out via the ×", async () => {
    mockGetCommitPipeline.mockResolvedValue({ nodes: [], edges: [] })
    const { onClose } = renderView()

    const chip = screen.getByTestId("comparison-chip")
    expect(chip).toHaveTextContent("v1.2")
    expect(chip).toHaveTextContent("abc1234") // sha.slice(0, 7)

    fireEvent.click(screen.getByTestId("comparison-chip-close"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("rings removed/changed on the left and added/changed on the right", async () => {
    // Historical: keep, changed(v1), removed.  Current: keep, changed(v2), added.
    mockGetCommitPipeline.mockResolvedValue({
      nodes: [node("keep"), node("edit", { v: 1 }), node("gone")],
      edges: [],
    })
    renderView({ currentNodes: [node("keep"), node("edit", { v: 2 }), node("fresh")] as never })

    await waitFor(() =>
      expect(screen.getByTestId("comparison-canvas-historical")).toBeInTheDocument(),
    )

    const left = within(screen.getByTestId("comparison-canvas-historical"))
    expect(left.getByTestId("cmp-node-gone")).toHaveClass("cmp-diff-removed")
    expect(left.getByTestId("cmp-node-edit")).toHaveClass("cmp-diff-changed")
    expect(left.getByTestId("cmp-node-keep").className).toBe("")

    const right = within(screen.getByTestId("comparison-canvas-current"))
    expect(right.getByTestId("cmp-node-fresh")).toHaveClass("cmp-diff-added")
    expect(right.getByTestId("cmp-node-edit")).toHaveClass("cmp-diff-changed")
    expect(right.getByTestId("cmp-node-keep").className).toBe("")

    const legend = screen.getByTestId("comparison-legend")
    expect(legend).toHaveTextContent("Added 1")
    expect(legend).toHaveTextContent("Changed 1")
    expect(legend).toHaveTextContent("Removed 1")
  })

  it("marks a moved-only node with the moved class on both canvases", async () => {
    mockGetCommitPipeline.mockResolvedValue({
      nodes: [node("shifted", {}, { x: 0, y: 0 })],
      edges: [],
    })
    renderView({ currentNodes: [node("shifted", {}, { x: 400, y: 200 })] as never })

    await waitFor(() =>
      expect(screen.getByTestId("comparison-canvas-historical")).toBeInTheDocument(),
    )
    const left = within(screen.getByTestId("comparison-canvas-historical"))
    const right = within(screen.getByTestId("comparison-canvas-current"))
    expect(left.getByTestId("cmp-node-shifted")).toHaveClass("cmp-diff-moved")
    expect(right.getByTestId("cmp-node-shifted")).toHaveClass("cmp-diff-moved")
    expect(screen.getByTestId("comparison-legend")).toHaveTextContent("Moved 1")
  })

  it("reports no differences when the graphs match", async () => {
    mockGetCommitPipeline.mockResolvedValue({ nodes: [node("keep")], edges: [] })
    renderView({ currentNodes: [node("keep")] as never })

    await waitFor(() => expect(screen.getByTestId("comparison-legend")).toBeInTheDocument())
    expect(screen.getByTestId("comparison-legend")).toHaveTextContent(
      "No differences from the current pipeline",
    )
  })

  it("emits an inspect payload (resolved on both sides) when a node is clicked", async () => {
    mockGetCommitPipeline.mockResolvedValue({
      nodes: [node("edit", { v: 1 })],
      edges: [],
    })
    const { onSelectNode } = renderView({ currentNodes: [node("edit", { v: 2 })] as never })

    await waitFor(() =>
      expect(screen.getByTestId("comparison-canvas-current")).toBeInTheDocument(),
    )

    const right = within(screen.getByTestId("comparison-canvas-current"))
    fireEvent.click(right.getByTestId("cmp-node-edit"))

    expect(onSelectNode).toHaveBeenCalledWith({
      id: "edit",
      status: "changed",
      historical: { label: "edit", nodeType: "polars", config: { v: 1 } },
      current: { label: "edit", nodeType: "polars", config: { v: 2 } },
    })
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
