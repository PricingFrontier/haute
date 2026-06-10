/**
 * Commit 6 frontend reds — port-aware Handles on apiInput nodes.
 *
 * Per `MULTI_FRAME_PLAN.md:615-660`:
 *  - apiInput with multiple emit:true tables renders one React Flow
 *    `<Handle>` per emit:true table; each Handle's `id` is the table's
 *    label.
 *  - apiInput with zero or one emit:true table renders the default
 *    single Handle (legacy behaviour preserved for single-port
 *    pipelines).
 *  - Handles are visually labelled (a small dot + the label string)
 *    so the user can drag-from-handle to identify the port.
 *
 * These are strict-TDD reds — they're expected to fail until the
 * PipelineNode commit-6 implementation lands.
 */
import { describe, it, expect, afterEach } from "vitest"
import { render, cleanup } from "@testing-library/react"
import { ReactFlowProvider, type NodeProps } from "@xyflow/react"

import PipelineNode from "../../nodes/PipelineNode"
import type { PipelineFlowNode, PipelineNodeData } from "../../types/node"
import { NODE_TYPES } from "../../utils/nodeTypes"

function renderNode(config: Record<string, unknown>) {
  const data: PipelineNodeData = {
    label: "quotes",
    nodeType: NODE_TYPES.API_INPUT,
    description: "",
    config,
  }
  const props = {
    id: "quotes",
    type: "custom",
    data,
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

describe("apiInput multi-port Handles (commit 6)", () => {
  afterEach(cleanup)

  it("renders one source Handle per emit:true table when multi-port", () => {
    const { container } = renderNode({
      path: "data/quotes.json",
      tables: [
        { path: "$[*]", label: "policies", emit: true, columns: [{ name: "a", selected: true }] },
        {
          path: "$[*].drivers[*]",
          label: "drivers",
          emit: true,
          columns: [{ name: "b", selected: true }],
        },
      ],
    })
    // React Flow Handles render as `.react-flow__handle` elements; the
    // source-side ones carry `data-handlepos="right"`. Multi-port =
    // exactly one source Handle per emit:true table.
    const sourceHandles = container.querySelectorAll(
      '.react-flow__handle[data-handlepos="right"]',
    )
    expect(sourceHandles).toHaveLength(2)
  })

  it("each multi-port Handle's id matches its table's label", () => {
    const { container } = renderNode({
      path: "data/quotes.json",
      tables: [
        { path: "$[*]", label: "policies", emit: true, columns: [{ name: "a", selected: true }] },
        {
          path: "$[*].drivers[*]",
          label: "drivers",
          emit: true,
          columns: [{ name: "b", selected: true }],
        },
      ],
    })
    const sourceHandles = Array.from(
      container.querySelectorAll('.react-flow__handle[data-handlepos="right"]'),
    )
    const ids = sourceHandles.map((h) => h.getAttribute("data-handleid")).sort()
    expect(ids).toEqual(["drivers", "policies"])
  })

  it("renders a single default source Handle when only one table is emit:true", () => {
    const { container } = renderNode({
      path: "data/quotes.json",
      tables: [
        { path: "$[*]", label: "policies", emit: true, columns: [] },
        { path: "$[*].drivers[*]", label: "drivers", emit: false, columns: [] },
      ],
    })
    // Single-port preserves legacy default-handle behaviour (id=null,
    // bare two-arg connect form). No special multi-port labelling.
    const sourceHandles = container.querySelectorAll(
      '.react-flow__handle[data-handlepos="right"]',
    )
    expect(sourceHandles).toHaveLength(1)
  })

  it("renders a single default source Handle when no tables are emit:true", () => {
    const { container } = renderNode({
      path: "data/quotes.json",
      tables: [
        { path: "$[*]", label: "policies", emit: false, columns: [] },
      ],
    })
    // Nothing to emit, but the node still needs a Handle in principle
    // (e.g. a future port could be added). Legacy single Handle is OK.
    const sourceHandles = container.querySelectorAll(
      '.react-flow__handle[data-handlepos="right"]',
    )
    expect(sourceHandles).toHaveLength(1)
  })

  it("renders a single default source Handle when config has no tables key (legacy/empty)", () => {
    const { container } = renderNode({ path: "data/quotes.json" })
    const sourceHandles = container.querySelectorAll(
      '.react-flow__handle[data-handlepos="right"]',
    )
    expect(sourceHandles).toHaveLength(1)
  })
})

// ─── W1.4 — handle ids are NEVER synthesized ─────────────────────────
//
// The backend keys runtime ports by the raw table label and hard-rejects
// blank/duplicate labels on save (`validate_v2_schema`). A synthesized
// `port_<idx>` / `label__<idx>` handle is therefore an id the executor
// can never resolve — an edge bound to one KeyErrors at run time. Tables
// with invalid labels get NO handle; the editor surfaces the validation.

function sourceHandleIds(container: HTMLElement): (string | null)[] {
  return Array.from(
    container.querySelectorAll('.react-flow__handle[data-handlepos="right"]'),
  ).map((h) => h.getAttribute("data-handleid"))
}

describe("apiInput Handles never synthesize ids (W1.4)", () => {
  afterEach(cleanup)

  it("a blank-label emit table renders NO handle — no port_<idx> fallback", () => {
    const { container } = renderNode({
      path: "data/quotes.json",
      tables: [
        { path: "$[*]", label: "", emit: true, columns: [{ name: "a", selected: true }] },
        {
          path: "$[*].drivers[*]",
          label: "drivers",
          emit: true,
          columns: [{ name: "b", selected: true }],
        },
      ],
    })
    const ids = sourceHandleIds(container)
    expect(ids).toEqual(["drivers"])
    expect(ids.some((id) => id?.startsWith("port_"))).toBe(false)
  })

  it("duplicate labels render ONE handle (first occurrence) — no __<idx> disambiguation", () => {
    const { container } = renderNode({
      path: "data/quotes.json",
      tables: [
        { path: "$[*]", label: "dup", emit: true, columns: [{ name: "a", selected: true }] },
        {
          path: "$[*].b[*]",
          label: "dup",
          emit: true,
          columns: [{ name: "b", selected: true }],
        },
      ],
    })
    const ids = sourceHandleIds(container)
    expect(ids).toEqual(["dup"])
  })

  it("all-blank labels fall back to the single default handle (no bindable fiction)", () => {
    // Backend-invalid config (only reachable from legacy disk files —
    // the editor refuses blank label commits). Nothing portlike is
    // invented for it.
    const { container } = renderNode({
      path: "data/quotes.json",
      tables: [
        { path: "$[*]", label: "", emit: true, columns: [{ name: "a", selected: true }] },
        { path: "$[*].b[*]", label: " ", emit: true, columns: [{ name: "b", selected: true }] },
      ],
    })
    const ids = sourceHandleIds(container)
    expect(ids).toHaveLength(1)
    expect(ids.some((id) => id?.startsWith("port_") || id?.includes("__"))).toBe(false)
  })
})
