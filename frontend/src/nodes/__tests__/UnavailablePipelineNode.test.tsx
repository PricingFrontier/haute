import { cleanup, render, screen } from "@testing-library/react"
import { ReactFlowProvider, type Node, type NodeProps } from "@xyflow/react"
import { afterEach, describe, expect, it } from "vitest"

import UnavailablePipelineNode from "../UnavailablePipelineNode"
import type { HauteNodeData } from "../../types/node"

type UnavailableNode = Node<HauteNodeData, "unavailablePipelineNode">

describe("UnavailablePipelineNode", () => {
  afterEach(cleanup)

  it("retains and announces the authored decorator without making handles connectable", () => {
    const props = {
      id: "removed@L9",
      type: "unavailablePipelineNode",
      data: {
        label: "Removed node",
        nodeType: "removed_type",
        _authoredDecorator: "removed_type",
        _loadAvailability: "unavailable",
      } satisfies HauteNodeData,
      selected: false,
      isConnectable: false,
      positionAbsoluteX: 0,
      positionAbsoluteY: 0,
      zIndex: 0,
      dragging: false,
      deletable: false,
      selectable: true,
    }

    render(
      <ReactFlowProvider>
        <UnavailablePipelineNode {...(props as unknown as NodeProps<UnavailableNode>)} />
      </ReactFlowProvider>,
    )

    expect(screen.getByRole("button", {
      name: "Unavailable removed_type node: Removed node",
    })).toBeInTheDocument()
    expect(screen.getByText("@pipeline.removed_type")).toBeInTheDocument()
  })
})
