import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import SubmodelPortEditor from "../../panels/editors/SubmodelPortEditor"
import type { SimpleNode } from "../../panels/editors"

afterEach(cleanup)

function inputBoundary(ports = [
  { id: "policy", label: "Policy data", parentEdges: [] },
  { id: "claims", label: "Claims history", parentEdges: [] },
]): SimpleNode {
  return {
    id: "submodel-input",
    type: "submodelPort",
    data: {
      label: "INPUT",
      description: "",
      nodeType: "submodelPort",
      config: {},
      instanceId: "pricing",
      definitionId: "definition_pricing",
      portDirection: "input",
      ports,
      externalNodeIds: [],
    },
  }
}

describe("SubmodelPortEditor", () => {
  it("uses the standard Inputs chips to remove a public input by port id", () => {
    const onDeleteInputPort = vi.fn()
    render(
      <SubmodelPortEditor
        node={inputBoundary()}
        onDeleteInputPort={onDeleteInputPort}
      />,
    )

    expect(screen.getByText("Inputs")).toBeInTheDocument()
    expect(screen.getByTestId("input-source-policy")).toHaveTextContent("Policy data")
    expect(screen.getByTestId("input-source-claims")).toHaveTextContent("Claims history")

    // The chip retires the shared public input across every occurrence, so it
    // must not reuse the ordinary "remove one connection" wording.
    const remove = screen.getByTitle(/^Remove public input "Policy data"/)
    expect(remove).toHaveAttribute(
      "title",
      'Remove public input "Policy data" from this submodel, including its '
      + "internal routes and every occurrence's connection",
    )
    expect(screen.queryByTitle(/^Remove connection from /)).not.toBeInTheDocument()

    fireEvent.click(remove)
    expect(onDeleteInputPort).toHaveBeenCalledOnce()
    expect(onDeleteInputPort).toHaveBeenCalledWith("policy")
  })

  it("renders the same frame list without remove controls when read-only", () => {
    render(<SubmodelPortEditor node={inputBoundary()} />)

    expect(screen.getByTestId("input-source-policy")).toBeInTheDocument()
    expect(screen.queryByTitle(/^Remove public input /)).not.toBeInTheDocument()
  })

  it("shows an intentional empty state for an input boundary with no frames", () => {
    render(<SubmodelPortEditor node={inputBoundary([])} onDeleteInputPort={vi.fn()} />)

    expect(screen.getByText("No input frames")).toBeInTheDocument()
  })

  it("does not present input controls for the Output boundary", () => {
    const output = inputBoundary([])
    output.data.portDirection = "output"

    const { container } = render(
      <SubmodelPortEditor node={output} onDeleteInputPort={vi.fn()} />,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it("fails visibly when boundary projection data is malformed", () => {
    const malformed = inputBoundary()
    malformed.data.ports = [{ id: "policy", label: "Policy data" }]

    render(<SubmodelPortEditor node={malformed} />)

    expect(screen.getByRole("alert")).toHaveTextContent("boundary data is invalid")
  })
})
