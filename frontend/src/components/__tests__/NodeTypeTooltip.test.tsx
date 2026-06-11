/**
 * tooltips-descriptions §5.2-B — the type-description content card.
 *
 * Exact-shape spirit applied to the rendered card: name/description text
 * must equal the NODE_TYPE_META strings exactly (not substring), and the
 * conditional constraint notes carry named-absence tests — a stray
 * constraint note on the wrong type is the bug class being defended.
 */
import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"

import NodeTypeTooltip from "../NodeTypeTooltip"
import { NODE_TYPE_META, NODE_TYPES, type NodeTypeValue } from "../../utils/nodeTypes"

describe("NodeTypeTooltip", () => {
  afterEach(cleanup)

  it("renders name and description exactly as in NODE_TYPE_META (dataSource)", () => {
    render(<NodeTypeTooltip type={NODE_TYPES.DATA_SOURCE} />)
    expect(screen.getByTestId("node-type-tooltip-name").textContent).toBe(
      NODE_TYPE_META.dataSource.name,
    )
    expect(screen.getByTestId("node-type-tooltip-description").textContent).toBe(
      NODE_TYPE_META.dataSource.description,
    )
  })

  it("renders the UPPERCASE type label badge", () => {
    render(<NodeTypeTooltip type={NODE_TYPES.DATA_SOURCE} />)
    expect(screen.getByText(NODE_TYPE_META.dataSource.label)).toBeInTheDocument()
  })

  it("carries data-node-type so tests and the harness can identify the described type", () => {
    const { container } = render(<NodeTypeTooltip type={NODE_TYPES.POLARS} />)
    expect(container.querySelector('[data-node-type="polars"]')).not.toBeNull()
  })

  it("shows the singleton note for singleton types (apiInput)", () => {
    render(<NodeTypeTooltip type={NODE_TYPES.API_INPUT} />)
    expect(screen.getByTestId("node-type-tooltip-singleton-note").textContent).toBe(
      "Only one per pipeline.",
    )
  })

  it("shows the blocked copy when singletonBlocked (palette disabled state)", () => {
    render(<NodeTypeTooltip type={NODE_TYPES.API_INPUT} singletonBlocked />)
    expect(screen.getByTestId("node-type-tooltip-singleton-note").textContent).toBe(
      "Already in this pipeline — only one allowed.",
    )
  })

  it("does NOT render a singleton note for non-singleton types (polars)", () => {
    // Named absence: a stray constraint note on the wrong type is the bug class.
    render(<NodeTypeTooltip type={NODE_TYPES.POLARS} />)
    expect(screen.queryByTestId("node-type-tooltip-singleton-note")).not.toBeInTheDocument()
  })

  it("shows the single-input note for maxInputs: 1 types (banding)", () => {
    render(<NodeTypeTooltip type={NODE_TYPES.BANDING} />)
    expect(screen.getByTestId("node-type-tooltip-maxinputs-note").textContent).toBe(
      "Single input.",
    )
  })

  it("shows the two-input note for maxInputs: 2 types (edgeJoin)", () => {
    render(<NodeTypeTooltip type={NODE_TYPES.EDGE_JOIN} />)
    expect(screen.getByTestId("node-type-tooltip-maxinputs-note").textContent).toBe(
      "Two inputs: base + join.",
    )
  })

  it("does NOT render an input-count note for unlimited-input types (polars)", () => {
    // Named absence: unlimited-input types must not claim an input limit.
    render(<NodeTypeTooltip type={NODE_TYPES.POLARS} />)
    expect(screen.queryByTestId("node-type-tooltip-maxinputs-note")).not.toBeInTheDocument()
  })

  it("renders nothing at all for unknown node types", () => {
    const { container } = render(<NodeTypeTooltip type={"mystery" as NodeTypeValue} />)
    expect(container).toBeEmptyDOMElement()
  })

  it("renders the forward-compat footer slot below a divider when provided", () => {
    // The item-6 boundary (§3.3): above the divider is type-static content;
    // the footer is reserved for future per-instance description content.
    render(
      <NodeTypeTooltip type={NODE_TYPES.POLARS} footer={<span>instance blurb</span>} />,
    )
    expect(screen.getByTestId("node-type-tooltip-footer")).toHaveTextContent("instance blurb")
  })

  it("renders no footer container when the slot is unused (this build)", () => {
    render(<NodeTypeTooltip type={NODE_TYPES.POLARS} />)
    expect(screen.queryByTestId("node-type-tooltip-footer")).not.toBeInTheDocument()
  })
})
