/**
 * Contract tests for Bundle 3c — React Flow handle remeasurement and
 * the full-detail apiInput frame-row body.
 *
 * 1. **Edge attachment via `useUpdateNodeInternals`**. React Flow caches
 *    handle measurements, so PipelineNode must request remeasurement when
 *    the ordered `apiInputFrameLabels` signature changes. The collision-safe
 *    JSON signature deliberately ignores edits that preserve that list,
 *    avoiding unnecessary handle churn.
 *
 * 2. **Full-detail frame-row body**. Every runtime-eligible emitted frame,
 *    including the sole frame of a single-table config, is shown as a named
 *    row. No frame-name rows render for a zero-eligible config or a non-apiInput
 *    node. Runtime eligibility requires `emit: true`, at least one selected
 *    column, and a valid raw label.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import { ReactFlowProvider, type NodeProps } from "@xyflow/react"

import PipelineNode from "../../nodes/PipelineNode"
import type { PipelineFlowNode, PipelineNodeData } from "../../types/node"
import { NODE_TYPES } from "../../utils/nodeTypes"
import { apiInputFrameLabels } from "../../utils/apiInputPorts"

function identity(config: Record<string, unknown>): Record<string, string> {
  const labels = apiInputFrameLabels(config, new Set())
  return Object.fromEntries(labels.map((label) => [label, label]))
}

function withIdentity(data: PipelineNodeData): PipelineNodeData {
  return {
    ...data,
    _sourceHandleInputNames: identity((data.config as Record<string, unknown>) ?? {}),
  }
}

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
    ...(data.nodeType === NODE_TYPES.API_INPUT
      ? { _sourceHandleInputNames: identity((data.config as Record<string, unknown>) ?? {}) }
      : {}),
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
// 1. useUpdateNodeInternals — handle topology/placement re-anchor
// ---------------------------------------------------------------------------

describe("Bundle 3c — useUpdateNodeInternals tracks apiInput frame labels", () => {
  it("calls updateNodeInternals with the node id on first render", () => {
    renderNode(
      {
        label: "Quote Input",
        nodeType: NODE_TYPES.API_INPUT,
        config: {
          tables: [
            { label: "row", emit: true, path: "$[:]", columns: [] },
            { label: "ext", emit: true, path: "$[:].ext[:]", columns: [] },
          ],
        },
      },
      "api_1",
    )
    // useEffect fires after mount; updateNodeInternals should have been
    // called with the node id.
    expect(mockUpdateNodeInternals).toHaveBeenCalledWith("api_1")
  })

  it("re-fires updateNodeInternals when the ordered frame-label list changes", () => {
    const { rerender } = renderNode(
      {
        label: "Quote Input",
        nodeType: NODE_TYPES.API_INPUT,
        config: {
          tables: [
            { label: "row", emit: true, path: "$[:]", columns: [{ name: "c", selected: true }] },
            {
              label: "ext",
              emit: true,
              path: "$[:].ext[:]",
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
          { label: "row", emit: true, path: "$[:]", columns: [{ name: "c", selected: true }] },
          {
            label: "ext",
            emit: true,
            path: "$[:].ext[:]",
            columns: [{ name: "c", selected: true }],
          },
          {
            label: "loss",
            emit: true,
            path: "$[:].loss[:]",
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
            data: withIdentity(updatedData),
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

  it("does NOT re-fire when an edit preserves the frame-label list", () => {
    // Adding a second selected column to an already-eligible table changes
    // neither its visible frame nor its handle id. The effect signature
    // remains stable, so React Flow must not be asked to remeasure.
    const initialData: PipelineNodeData = {
      label: "Quote Input",
      description: "",
      nodeType: NODE_TYPES.API_INPUT,
      config: {
        tables: [
          {
            label: "row",
            emit: true,
            path: "$[:]",
            columns: [{ name: "existing", path: "$[:].existing", type: "int", selected: true }],
          },
          {
            label: "ext",
            emit: true,
            path: "$[:].ext[:]",
            columns: [{ name: "c", selected: true }],
          },
        ],
      },
    }
    const { rerender } = render(
      <ReactFlowProvider>
        <PipelineNode
          {...({
            id: "api_1",
            type: "custom",
            data: withIdentity(initialData),
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

    // Add another selected column without changing table eligibility.
    const editedData: PipelineNodeData = {
      ...initialData,
      config: {
        tables: [
          {
            label: "row",
            emit: true,
            path: "$[:]",
            columns: [
              { name: "existing", path: "$[:].existing", type: "int", selected: true },
              { name: "added_column", path: "$[:].x", type: "int", selected: true },
            ],
          },
          {
            label: "ext",
            emit: true,
            path: "$[:].ext[:]",
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
            data: withIdentity(editedData),
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

  it("re-fires when valid identifier labels are reordered", () => {
    const eligibleTables = (labels: string[]) =>
      labels.map((label) => ({
        label,
        emit: true,
        path: `$['${label}']`,
        columns: [{ name: "value", selected: true }],
      }))
    const initialData: PipelineNodeData = {
      label: "Quote Input",
      description: "",
      nodeType: NODE_TYPES.API_INPUT,
      config: { tables: eligibleTables(["alpha", "beta"]) },
    }
    const { rerender } = render(
      <ReactFlowProvider>
        <PipelineNode
          {...({
            id: "api_1",
            type: "custom",
            data: withIdentity(initialData),
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

    rerender(
      <ReactFlowProvider>
        <PipelineNode
          {...({
            id: "api_1",
            type: "custom",
            data: withIdentity({
              ...initialData,
              config: { tables: eligibleTables(["beta", "alpha"]) },
            }),
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

    expect(mockUpdateNodeInternals.mock.calls.length).toBe(callCountAfterMount + 1)
    expect(mockUpdateNodeInternals).toHaveBeenLastCalledWith("api_1")
  })

  it("re-fires when a table becomes the sole visible frame without changing the emit-table set", () => {
    const initialData: PipelineNodeData = {
      label: "Quote Input",
      description: "",
      nodeType: NODE_TYPES.API_INPUT,
      config: {
        tables: [
          { label: "row", emit: true, path: "$[:]", columns: [] },
        ],
      },
    }
    const { rerender } = render(
      <ReactFlowProvider>
        <PipelineNode
          {...({
            id: "api_1",
            type: "custom",
            data: withIdentity(initialData),
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

    const eligibleData: PipelineNodeData = {
      ...initialData,
      config: {
        tables: [
          {
            label: "row",
            emit: true,
            path: "$[:]",
            columns: [{ name: "first_selected", selected: true }],
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
            data: withIdentity(eligibleData),
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
    expect(mockUpdateNodeInternals).toHaveBeenLastCalledWith("api_1")
  })
})

// ---------------------------------------------------------------------------
// 2. Full-detail apiInput frame-row body
// ---------------------------------------------------------------------------

describe("Bundle 3c — full-detail node body shows eligible emitted frames", () => {
  it("renders one named frame row per eligible emitted frame", () => {
    renderNode({
      label: "Quote Input",
      nodeType: NODE_TYPES.API_INPUT,
      config: {
        tables: [
          { label: "row", emit: true, path: "$[:]", columns: [{ name: "c", selected: true }] },
          {
            label: "ext",
            emit: true,
            path: "$[:].ext[:]",
            columns: [{ name: "c", selected: true }],
          },
        ],
      },
    })
    expect(screen.getAllByTestId(/^api-input-frame-row-/).map((row) => row.dataset.testid)).toEqual([
      "api-input-frame-row-row",
      "api-input-frame-row-ext",
    ])
    expect(screen.getByTestId("api-input-body-label-row")).toBeInTheDocument()
    expect(screen.getByTestId("api-input-body-label-ext")).toBeInTheDocument()
    // Visual content matches the label text.
    expect(screen.getByTestId("api-input-body-label-row")).toHaveTextContent("row")
    expect(screen.getByTestId("api-input-body-label-ext")).toHaveTextContent("ext")
  })

  it("renders the name row for the sole eligible frame in a single-table config", () => {
    renderNode({
      label: "Quote Input",
      nodeType: NODE_TYPES.API_INPUT,
      config: {
        tables: [
          { label: "row", emit: true, path: "$[:]", columns: [{ name: "c", selected: true }] },
        ],
      },
    })
    expect(screen.getByTestId("api-input-frame-row-row")).toBeInTheDocument()
    expect(screen.getByTestId("api-input-body-label-row")).toHaveTextContent("row")
  })

  it("does NOT render frame-name rows for an apiInput with zero eligible frames", () => {
    renderNode({
      label: "Quote Input",
      nodeType: NODE_TYPES.API_INPUT,
      config: {
        tables: [
          { label: "row", emit: false, path: "$[:]", columns: [{ name: "c", selected: true }] },
        ],
      },
    })
    expect(screen.queryByTestId(/^api-input-frame-row-/)).not.toBeInTheDocument()
    expect(screen.queryByTestId(/^api-input-body-label-/)).not.toBeInTheDocument()
  })

  it("does NOT render frame-name rows for non-apiInput nodes (e.g. polars)", () => {
    renderNode({
      label: "Clean Data",
      nodeType: NODE_TYPES.POLARS,
      config: {
        // even with a tables-shaped config, the labels are apiInput-only
        tables: [
          { label: "row", emit: true, path: "$[:]", columns: [{ name: "c", selected: true }] },
          { label: "ext", emit: true, path: "$[:].ext[:]", columns: [{ name: "c", selected: true }] },
        ],
      },
    })
    expect(screen.queryByTestId(/^api-input-frame-row-/)).not.toBeInTheDocument()
    expect(screen.queryByTestId(/^api-input-body-label-/)).not.toBeInTheDocument()
  })
})
