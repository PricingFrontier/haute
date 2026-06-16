/**
 * Contract tests for Bundle 3c — edge attachment + node body
 * table-label list.
 *
 * 1. **Edge attachment via `useUpdateNodeInternals`**. When an apiInput
 *    node's emit-table set changes (a table is added, removed, or its
 *    `emit` flag toggled), the handle topology on the right edge
 *    changes too: 0/1 emit → single unlabelled Handle; 2+ emit → one
 *    labelled Handle per table (see `_SourceHandles` in
 *    `PipelineNode.tsx`). React Flow caches each node's handle
 *    measurements when the node first mounts; without an explicit
 *    `updateNodeInternals(id)` call, edges connected to the old
 *    handles can render at stale screen coordinates after the topology
 *    changes — the visible symptom is edges "floating" off the node or
 *    snapping to (0,0). The hook re-measures the handles for the given
 *    node id; calling it from a `useEffect` keyed on the emit-label
 *    signature is the idiomatic React Flow pattern for this.
 *
 * 2. **Compact right-aligned table-label list on the node body**. For
 *    apiInput nodes with 2+ emit tables (the multi-port case), render
 *    each emit table's label as a small right-aligned text item on the
 *    node body, vertically stacked top-to-bottom in the same order as
 *    the handles on the right edge. Gives the user a visual mapping
 *    from each port to its source table without having to open the
 *    panel.
 *
 *    Not rendered when:
 *      - The node is not an apiInput.
 *      - The apiInput has fewer than 2 emit tables (single-port
 *        fallback — the unlabelled Handle is unambiguous).
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import { ReactFlowProvider, type NodeProps } from "@xyflow/react"

import PipelineNode from "../../nodes/PipelineNode"
import type { PipelineFlowNode, PipelineNodeData } from "../../types/node"
import { NODE_TYPES } from "../../utils/nodeTypes"

// ---------------------------------------------------------------------------
// Mock useUpdateNodeInternals so we can assert it gets called on handle
// topology changes. Keep the rest of @xyflow/react real.
// ---------------------------------------------------------------------------

const mockUpdateNodeInternals = vi.fn()
vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react")
  return {
    ...actual,
    useUpdateNodeInternals: () => mockUpdateNodeInternals,
  }
})

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderNode(
  data: Partial<PipelineNodeData> & { label: string; nodeType: string },
  nodeId = "test-node",
) {
  const fullData: PipelineNodeData = {
    description: "",
    ...data,
  }
  const props = {
    id: nodeId,
    type: "custom",
    data: fullData,
    selected: false,
    isConnectable: true,
    positionAbsoluteX: 0,
    positionAbsoluteY: 0,
    zIndex: 0,
    dragging: false,
    deletable: true,
    selectable: true,
    parentId: undefined,
    sourcePosition: undefined,
    targetPosition: undefined,
    dragHandle: undefined,
  }
  return render(
    <ReactFlowProvider>
      <PipelineNode {...(props as unknown as NodeProps<PipelineFlowNode>)} />
    </ReactFlowProvider>,
  )
}

afterEach(() => {
  cleanup()
  mockUpdateNodeInternals.mockReset()
})

// ---------------------------------------------------------------------------
// 1. useUpdateNodeInternals — handle topology re-anchor
// ---------------------------------------------------------------------------

describe("Bundle 3c — useUpdateNodeInternals fires when emit-table set changes", () => {
  it("calls updateNodeInternals with the node id on first render", () => {
    renderNode(
      {
        label: "Quote Input",
        nodeType: NODE_TYPES.API_INPUT,
        config: {
          tables: [
            { label: "row", emit: true, path: "$[*]", columns: [] },
            { label: "ext", emit: true, path: "$[*].ext[*]", columns: [] },
          ],
        },
      },
      "api_1",
    )
    // useEffect fires after mount; updateNodeInternals should have been
    // called with the node id.
    expect(mockUpdateNodeInternals).toHaveBeenCalledWith("api_1")
  })

  it("re-fires updateNodeInternals when the emit-table signature changes", () => {
    const { rerender } = renderNode(
      {
        label: "Quote Input",
        nodeType: NODE_TYPES.API_INPUT,
        config: {
          tables: [
            { label: "row", emit: true, path: "$[*]", columns: [{ name: "c", selected: true }] },
            {
              label: "ext",
              emit: true,
              path: "$[*].ext[*]",
              columns: [{ name: "c", selected: true }],
            },
          ],
        },
      },
      "api_1",
    )
    const callCountAfterMount = mockUpdateNodeInternals.mock.calls.length

    // Add another emit table — handle topology grows from 2 to 3.
    const updatedData: PipelineNodeData = {
      label: "Quote Input",
      description: "",
      nodeType: NODE_TYPES.API_INPUT,
      config: {
        tables: [
          { label: "row", emit: true, path: "$[*]", columns: [{ name: "c", selected: true }] },
          {
            label: "ext",
            emit: true,
            path: "$[*].ext[*]",
            columns: [{ name: "c", selected: true }],
          },
          {
            label: "loss",
            emit: true,
            path: "$[*].loss[*]",
            columns: [{ name: "c", selected: true }],
          },
        ],
      },
    }
    rerender(
      <ReactFlowProvider>
        <PipelineNode
          {...({
            id: "api_1",
            type: "custom",
            data: updatedData,
            selected: false,
            isConnectable: true,
            positionAbsoluteX: 0,
            positionAbsoluteY: 0,
            zIndex: 0,
            dragging: false,
            deletable: true,
            selectable: true,
          } as unknown as NodeProps<PipelineFlowNode>)}
        />
      </ReactFlowProvider>,
    )
    expect(mockUpdateNodeInternals.mock.calls.length).toBeGreaterThan(callCountAfterMount)
    // Most recent call is for our node.
    expect(mockUpdateNodeInternals).toHaveBeenLastCalledWith("api_1")
  })

  it("does NOT re-fire when a non-handle-affecting field changes (label edit on existing table)", () => {
    // A column or non-emit attribute change doesn't change the handle
    // set; the effect's signature dependency should NOT trigger again.
    const initialData: PipelineNodeData = {
      label: "Quote Input",
      description: "",
      nodeType: NODE_TYPES.API_INPUT,
      config: {
        tables: [
          { label: "row", emit: true, path: "$[*]", columns: [] },
          { label: "ext", emit: true, path: "$[*].ext[*]", columns: [] },
        ],
      },
    }
    const { rerender } = render(
      <ReactFlowProvider>
        <PipelineNode
          {...({
            id: "api_1",
            type: "custom",
            data: initialData,
            selected: false,
            isConnectable: true,
            positionAbsoluteX: 0,
            positionAbsoluteY: 0,
            zIndex: 0,
            dragging: false,
            deletable: true,
            selectable: true,
          } as unknown as NodeProps<PipelineFlowNode>)}
        />
      </ReactFlowProvider>,
    )
    const callCountAfterMount = mockUpdateNodeInternals.mock.calls.length

    // Edit a column inside an existing table — does NOT change emit
    // labels.  Effect signature stays the same; hook should not fire
    // again.
    const editedData: PipelineNodeData = {
      ...initialData,
      config: {
        tables: [
          {
            label: "row",
            emit: true,
            path: "$[*]",
            columns: [{ name: "added_column", path: "$[*].x", type: "int", selected: true }],
          },
          { label: "ext", emit: true, path: "$[*].ext[*]", columns: [] },
        ],
      },
    }
    rerender(
      <ReactFlowProvider>
        <PipelineNode
          {...({
            id: "api_1",
            type: "custom",
            data: editedData,
            selected: false,
            isConnectable: true,
            positionAbsoluteX: 0,
            positionAbsoluteY: 0,
            zIndex: 0,
            dragging: false,
            deletable: true,
            selectable: true,
          } as unknown as NodeProps<PipelineFlowNode>)}
        />
      </ReactFlowProvider>,
    )
    expect(mockUpdateNodeInternals.mock.calls.length).toBe(callCountAfterMount)
  })
})

// ---------------------------------------------------------------------------
// 2. Compact right-aligned table-label list on the node body
// ---------------------------------------------------------------------------

describe("Bundle 3c — node body shows compact table-label list", () => {
  it("renders emit table labels on the body for apiInput with 2+ emit tables", () => {
    renderNode({
      label: "Quote Input",
      nodeType: NODE_TYPES.API_INPUT,
      config: {
        tables: [
          { label: "row", emit: true, path: "$[*]", columns: [{ name: "c", selected: true }] },
          {
            label: "ext",
            emit: true,
            path: "$[*].ext[*]",
            columns: [{ name: "c", selected: true }],
          },
        ],
      },
    })
    expect(screen.getByTestId("api-input-body-label-row")).toBeInTheDocument()
    expect(screen.getByTestId("api-input-body-label-ext")).toBeInTheDocument()
    // Visual content matches the label text.
    expect(screen.getByTestId("api-input-body-label-row")).toHaveTextContent("row")
    expect(screen.getByTestId("api-input-body-label-ext")).toHaveTextContent("ext")
  })

  it("does NOT render the label list for an apiInput with a single emit table", () => {
    renderNode({
      label: "Quote Input",
      nodeType: NODE_TYPES.API_INPUT,
      config: {
        tables: [
          { label: "row", emit: true, path: "$[*]", columns: [] },
        ],
      },
    })
    expect(screen.queryByTestId(/^api-input-body-label-/)).not.toBeInTheDocument()
  })

  it("does NOT render the label list for an apiInput with zero emit tables", () => {
    renderNode({
      label: "Quote Input",
      nodeType: NODE_TYPES.API_INPUT,
      config: {
        tables: [
          { label: "row", emit: false, path: "$[*]", columns: [] },
        ],
      },
    })
    expect(screen.queryByTestId(/^api-input-body-label-/)).not.toBeInTheDocument()
  })

  it("does NOT render the label list for non-apiInput nodes (e.g. polars)", () => {
    renderNode({
      label: "Clean Data",
      nodeType: NODE_TYPES.POLARS,
      config: {
        // even with a tables-shaped config, the labels are apiInput-only
        tables: [
          { label: "row", emit: true, path: "$[*]", columns: [] },
          { label: "ext", emit: true, path: "$[*].ext[*]", columns: [] },
        ],
      },
    })
    expect(screen.queryByTestId(/^api-input-body-label-/)).not.toBeInTheDocument()
  })
})
