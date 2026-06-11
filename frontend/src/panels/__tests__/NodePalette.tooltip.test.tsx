/**
 * tooltips-descriptions §5.2-C (render gate) + §5.2-F (no-double-tooltip).
 *
 * C is the rule-3 gate: every PALETTE_TYPES entry must surface its
 * NODE_TYPE_META description through the rich tooltip — a palette refactor
 * cannot silently drop a type's description.  (The 3 non-palette types —
 * submodel, submodelPort, edgeJoin — are data-gated in nodeTypes.test.ts
 * and surface-gated by the PipelineNode canvas tests.)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react"
import type { Node } from "@xyflow/react"

import NodePalette from "../NodePalette"
import { NODE_TYPE_META, NODE_TYPES, PALETTE_TYPES } from "../../utils/nodeTypes"

const PALETTE_DELAY_MS = 300

describe("NodePalette tooltips", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it.each(PALETTE_TYPES)(
    "hovering the %s palette item surfaces its exact NODE_TYPE_META description",
    (type) => {
      render(<NodePalette nodes={[]} />)
      fireEvent.mouseEnter(screen.getByTestId(`node-palette-item-${type}`))
      act(() => {
        vi.advanceTimersByTime(PALETTE_DELAY_MS)
      })
      expect(screen.getByTestId("node-type-tooltip-description").textContent).toBe(
        NODE_TYPE_META[type].description,
      )
      expect(screen.getByTestId("node-type-tooltip-name").textContent).toBe(
        NODE_TYPE_META[type].name,
      )
    },
  )

  it("palette items carry no native title attribute", () => {
    // Named absence — bug class: native + custom tooltip both rendering.
    render(<NodePalette nodes={[]} />)
    for (const type of PALETTE_TYPES) {
      expect(screen.getByTestId(`node-palette-item-${type}`)).not.toHaveAttribute("title")
    }
  })

  it("keeps the existing node-palette-item-<type> testids as the tooltip triggers", () => {
    // One testid per element: the trigger is the palette item itself, no
    // extra trigger testid is introduced on this surface.
    render(<NodePalette nodes={[]} />)
    expect(screen.queryByTestId("node-type-tooltip-trigger")).not.toBeInTheDocument()
  })

  it("disabled singleton tooltip shows the already-in-pipeline warning copy", () => {
    const nodes: Node[] = [
      { id: "ai1", data: { label: "Quote Input", nodeType: NODE_TYPES.API_INPUT } } as unknown as Node,
    ]
    render(<NodePalette nodes={nodes} />)
    fireEvent.mouseEnter(screen.getByTestId(`node-palette-item-${NODE_TYPES.API_INPUT}`))
    act(() => {
      vi.advanceTimersByTime(PALETTE_DELAY_MS)
    })
    expect(screen.getByTestId("node-type-tooltip-singleton-note").textContent).toBe(
      "Already in this pipeline — only one allowed.",
    )
  })

  it("palette items are keyboard-focusable and show the tooltip on focus without delay", () => {
    render(<NodePalette nodes={[]} />)
    const item = screen.getByTestId(`node-palette-item-${NODE_TYPES.DATA_SOURCE}`)
    expect(item).toHaveAttribute("tabindex", "0")
    fireEvent.focus(item)
    expect(screen.getByTestId("node-type-tooltip")).toBeInTheDocument()
  })

  it("drag start dismisses the tooltip via pointerdown before the drag begins", () => {
    render(<NodePalette nodes={[]} />)
    const item = screen.getByTestId(`node-palette-item-${NODE_TYPES.POLARS}`)
    fireEvent.mouseEnter(item)
    act(() => {
      vi.advanceTimersByTime(PALETTE_DELAY_MS)
    })
    expect(screen.getByTestId("node-type-tooltip")).toBeInTheDocument()
    fireEvent.pointerDown(item)
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })
})
