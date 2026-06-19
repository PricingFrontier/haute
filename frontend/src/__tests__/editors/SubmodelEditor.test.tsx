/**
 * Render tests for SubmodelEditor.
 *
 * The side-pane derives its I/O frames from the parent graph's cross-boundary
 * edges (via buildSubmodelBoundary) — frames map 1-1 onto edges — so these tests
 * drive the editor through edges, not the coarser config.inputPorts list.
 */
import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent } from "@testing-library/react"
import type { Edge } from "@xyflow/react"
import SubmodelEditor from "../../panels/editors/SubmodelEditor"

afterEach(cleanup)

const SM = "submodel__sm"

function edge(
  id: string,
  source: string,
  target: string,
  opts: { sourceHandle?: string; targetHandle?: string } = {},
): Edge {
  return { id, source, target, ...opts } as unknown as Edge
}

function renderEditor(
  opts: { childNodeIds?: string[]; file?: string; edges?: Edge[] } = {},
) {
  const config: Record<string, unknown> = {
    childNodeIds: opts.childNodeIds ?? [],
    ...(opts.file !== undefined ? { file: opts.file } : {}),
  }
  return render(
    <SubmodelEditor config={config} accentColor="#64748b" nodeId={SM} edges={opts.edges ?? []} />,
  )
}

describe("SubmodelEditor", () => {
  it("renders Wrapper badge text", () => {
    renderEditor()
    expect(screen.getByText("Wrapper")).toBeTruthy()
  })

  it("shows node count from childNodeIds", () => {
    renderEditor({ childNodeIds: ["node_1", "node_2", "node_3"] })
    expect(screen.getByText("3 nodes")).toBeTruthy()
  })

  it("renders file path when config.file is set", () => {
    renderEditor({ file: "pipelines/sub_model.py" })
    expect(screen.getByText("File")).toBeTruthy()
    expect(screen.getByText("pipelines/sub_model.py")).toBeTruthy()
  })

  it("does NOT render file section when config.file is empty", () => {
    renderEditor({ file: "" })
    expect(screen.queryByText("File")).toBeNull()
  })

  it("does NOT render I/O sections when there are no boundary frames", () => {
    renderEditor({ childNodeIds: ["c1"] })
    expect(screen.queryByText("Inputs")).toBeNull()
    expect(screen.queryByText("Outputs")).toBeNull()
  })

  it("shows double-click hint", () => {
    renderEditor()
    expect(screen.getByText("Double-click to view internal nodes")).toBeTruthy()
  })
})

describe("SubmodelEditor — per-frame I/O (derived from edges)", () => {
  it("renders one input frame row per cross-boundary input link", () => {
    renderEditor({
      childNodeIds: ["c1", "c2"],
      edges: [
        edge("e1", "src_a", SM, { targetHandle: "in__c1" }),
        edge("e2", "src_b", SM, { targetHandle: "in__c2" }),
      ],
    })
    expect(screen.getByText("Inputs")).toBeTruthy()
    expect(screen.getByTestId("wrapper-frame-input-c1")).toBeTruthy()
    expect(screen.getByTestId("wrapper-frame-input-c2")).toBeTruthy()
  })

  it("renders one output frame row per emitting node, invariant to consumer count", () => {
    renderEditor({
      childNodeIds: ["c1"],
      edges: [
        edge("e1", SM, "y1", { sourceHandle: "out__c1" }),
        edge("e2", SM, "y2", { sourceHandle: "out__c1" }), // 2nd consumer, same frame
      ],
    })
    expect(screen.getByText("Outputs")).toBeTruthy()
    expect(screen.getAllByTestId("wrapper-frame-output-c1")).toHaveLength(1)
  })

  // Frames map 1-1 onto edges: two links into one node are two input frames.
  it("shows two input frames when one node takes two external links", () => {
    renderEditor({
      childNodeIds: ["c1"],
      edges: [
        edge("e1", "src_a", SM, { targetHandle: "in__c1" }),
        edge("e2", "src_b", SM, { targetHandle: "in__c1" }),
      ],
    })
    expect(screen.getAllByTestId("wrapper-frame-input-c1")).toHaveLength(2)
  })

  // Render-gate (AGENTS.md §UI Test Assertions rule 3): every frame surfaces.
  it("surfaces every input and output frame (none silently dropped)", () => {
    renderEditor({
      childNodeIds: ["a", "b", "x", "y"],
      edges: [
        edge("e1", "s", SM, { targetHandle: "in__a" }),
        edge("e2", "s", SM, { targetHandle: "in__b" }),
        edge("e3", SM, "t1", { sourceHandle: "out__x" }),
        edge("e4", SM, "t2", { sourceHandle: "out__y" }),
      ],
    })
    expect(screen.getByTestId("wrapper-frame-input-a")).toBeTruthy()
    expect(screen.getByTestId("wrapper-frame-input-b")).toBeTruthy()
    expect(screen.getByTestId("wrapper-frame-output-x")).toBeTruthy()
    expect(screen.getByTestId("wrapper-frame-output-y")).toBeTruthy()
  })

  it("collapses frame detail by default and expands it on click", () => {
    renderEditor({ childNodeIds: ["c1"], edges: [edge("e1", SM, "y", { sourceHandle: "out__c1" })] })
    const row = screen.getByTestId("wrapper-frame-output-c1")
    expect(row.getAttribute("aria-expanded")).toBe("false")
    expect(screen.queryByText(/arrive with the wrapper output model/)).toBeNull()

    fireEvent.click(row)
    expect(row.getAttribute("aria-expanded")).toBe("true")
    expect(screen.getByText(/arrive with the wrapper output model/)).toBeTruthy()
    expect(screen.getByText(/Output frame produced by node/)).toBeTruthy()
  })

  it("input frame detail names its binding node", () => {
    renderEditor({ childNodeIds: ["c1"], edges: [edge("e1", "s", SM, { targetHandle: "in__c1" })] })
    fireEvent.click(screen.getByTestId("wrapper-frame-input-c1"))
    expect(screen.getByText(/Input frame feeding node/)).toBeTruthy()
  })

  it("drops a frame whose child is not a member (stale handle)", () => {
    renderEditor({ childNodeIds: ["c1"], edges: [edge("e1", "s", SM, { targetHandle: "in__ghost" })] })
    expect(screen.queryByText("Inputs")).toBeNull()
    expect(screen.queryAllByTestId(/^wrapper-frame-/)).toHaveLength(0)
  })
})
