import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react"
import EdgeJoinEditor from "../../panels/editors/EdgeJoinEditor"
import { GraphProvider } from "../../panels/GraphContext"
import type { OnUpdateConfig, SimpleEdge, SimpleNode } from "../../panels/editors"

afterEach(cleanup)

const baseNode: SimpleNode = {
  id: "quotes",
  data: {
    label: "Quotes",
    description: "",
    nodeType: "dataInput",
    _columns: [
      { name: "policy_id", dtype: "String" },
      { name: "state", dtype: "String" },
      { name: "premium", dtype: "Float64" },
    ],
  },
}

const joinNode: SimpleNode = {
  id: "lookup",
  data: {
    label: "Area Lookup",
    description: "",
    nodeType: "dataInput",
    _columns: [
      { name: "policy_id", dtype: "String" },
      { name: "lookup_policy_id", dtype: "String" },
      { name: "state", dtype: "String" },
      { name: "region", dtype: "String" },
    ],
  },
}

const edgeJoinNode: SimpleNode = {
  id: "edge_join_1",
  data: {
    label: "Edge Join",
    description: "",
    nodeType: "edgeJoin",
    config: {},
  },
}

const edges = [
  { id: "e_quotes_join", source: "quotes", target: "edge_join_1", targetHandle: "base" },
  { id: "e_lookup_join", source: "lookup", target: "edge_join_1", targetHandle: "join" },
] as SimpleEdge[]

function renderEditor(
  config: Record<string, unknown>,
  onUpdate: OnUpdateConfig = vi.fn(() => ({ ok: true as const })),
  overrides: {
    edges?: SimpleEdge[]
    onDeleteInput?: (edgeId: string) => void
    onSwapInputs?: () => void
  } = {},
) {
  const onSwapInputs = overrides.onSwapInputs ?? vi.fn()
  const result = render(
    <GraphProvider allNodes={[baseNode, joinNode, edgeJoinNode]} edges={overrides.edges ?? edges}>
      <EdgeJoinEditor
        config={config}
        onUpdate={onUpdate}
        nodeId="edge_join_1"
        accentColor="#0ea5e9"
        onDeleteInput={overrides.onDeleteInput}
        onSwapInputs={onSwapInputs}
      />
    </GraphProvider>,
  )
  return { ...result, onUpdate: onUpdate as ReturnType<typeof vi.fn> }
}

describe("EdgeJoinEditor", () => {
  it("does not render a duplicate edge-join title inside the config body", () => {
    renderEditor({
      baseInput: "quotes",
      joinInput: "lookup",
      how: "left",
      on: ["policy_id"],
    })

    expect(screen.queryByText("Edge Join")).not.toBeInTheDocument()
  })

  it("renders fixed canvas-derived input roles, join type, same-name keys, and suffix from config", () => {
    renderEditor({
      baseInput: "quotes",
      joinInput: "lookup",
      how: "left",
      on: ["policy_id"],
      suffix: "_lookup",
    })

    expect(screen.queryByRole("combobox", { name: "Base Input" })).not.toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "Join Input" })).not.toBeInTheDocument()
    expect(screen.getByText("Dominant Input")).toBeInTheDocument()
    expect(screen.getByText("Joining Input")).toBeInTheDocument()
    expect(screen.getAllByText("Quotes").length).toBeGreaterThan(0)
    expect(screen.getAllByText("Area Lookup").length).toBeGreaterThan(0)
    expect(screen.getByRole("button", { name: "Swap inputs" })).toBeEnabled()
    expect(screen.getByLabelText("Join Type")).toHaveValue("left")
    expect(screen.getByRole("radio", { name: "Same-name keys" })).toHaveAttribute("aria-checked", "true")
    expect(screen.getByLabelText("Same-name key 1")).toHaveValue("policy_id")
    expect(screen.getByLabelText("Suffix")).toHaveValue("_lookup")
  })

  it("uses the swap action instead of editable role dropdowns", () => {
    const onSwapInputs = vi.fn()
    const { onUpdate } = renderEditor({
      baseInput: "quotes",
      joinInput: "lookup",
      how: "left",
      on: ["policy_id"],
      suffix: "_lookup",
    }, vi.fn(), { onSwapInputs })

    fireEvent.click(screen.getByRole("button", { name: "Swap inputs" }))

    expect(onSwapInputs).toHaveBeenCalledOnce()
    expect(onUpdate).not.toHaveBeenCalledWith("baseInput", expect.anything())
    expect(onUpdate).not.toHaveBeenCalledWith("joinInput", expect.anything())
  })

  it("disables swapping until both canvas role inputs are connected", () => {
    renderEditor({
      baseInput: "quotes",
      joinInput: "lookup",
      how: "left",
      on: ["policy_id"],
    }, vi.fn(), { edges: [edges[0]] })

    expect(screen.getByRole("button", { name: "Swap inputs" })).toBeDisabled()
    expect(screen.getByText("Not connected")).toBeInTheDocument()
  })

  it("disables swapping when a role has ambiguous canvas connections", () => {
    renderEditor({
      baseInput: "quotes",
      joinInput: "lookup",
      how: "left",
      on: ["policy_id"],
    }, vi.fn(), {
      edges: [
        ...edges,
        { id: "e_other_base", source: "lookup", target: "edge_join_1", targetHandle: "base" },
      ],
    })

    expect(screen.getByRole("button", { name: "Swap inputs" })).toBeDisabled()
    expect(screen.getByRole("alert")).toHaveTextContent("Connect exactly one input to the base handle.")
  })

  it("retains delete actions for fixed role inputs", () => {
    const onDeleteInput = vi.fn()
    renderEditor({
      baseInput: "quotes",
      joinInput: "lookup",
      how: "left",
      on: ["policy_id"],
    }, vi.fn(), { onDeleteInput })

    fireEvent.click(screen.getByRole("button", { name: "Remove Dominant Input" }))
    expect(onDeleteInput).toHaveBeenCalledWith("e_quotes_join")
  })

  it("updates join type, same-name keys, and suffix", () => {
    const { onUpdate } = renderEditor({
      baseInput: "quotes",
      joinInput: "lookup",
      how: "left",
      on: ["policy_id"],
      suffix: "_lookup",
    })

    fireEvent.change(screen.getByLabelText("Join Type"), { target: { value: "inner" } })
    expect(onUpdate).toHaveBeenCalledWith("how", "inner")

    fireEvent.change(screen.getByLabelText("Same-name key 1"), { target: { value: "state" } })
    expect(onUpdate).toHaveBeenCalledWith({ on: ["state"], leftOn: [], rightOn: [] })

    fireEvent.change(screen.getByLabelText("Suffix"), { target: { value: "_dim" } })
    expect(onUpdate).not.toHaveBeenCalledWith("suffix", "_dim")
    fireEvent.blur(screen.getByLabelText("Suffix"))
    expect(onUpdate).toHaveBeenCalledWith("suffix", "_dim")
  })

  it("offers exactly the seven backend-supported join modes", () => {
    renderEditor({
      baseInput: "quotes",
      joinInput: "lookup",
      how: "left",
      on: ["policy_id"],
    })

    const values = within(screen.getByLabelText("Join Type"))
      .getAllByRole("option")
      .map((option) => (option as HTMLOptionElement).value)

    expect(values).toEqual(["left", "inner", "full", "right", "semi", "anti", "cross"])
  })

  it("clears every key representation when changing to a cross join", () => {
    const { onUpdate } = renderEditor({
      baseInput: "quotes",
      joinInput: "lookup",
      how: "left",
      leftOn: ["policy_id"],
      rightOn: ["lookup_policy_id"],
    })

    fireEvent.change(screen.getByLabelText("Join Type"), { target: { value: "cross" } })

    expect(onUpdate).toHaveBeenCalledWith({
      how: "cross",
      on: [],
      leftOn: [],
      rightOn: [],
    })
  })

  it("switches between same-name and paired key modes without leaving conflicting config", () => {
    const { onUpdate } = renderEditor({
      baseInput: "quotes",
      joinInput: "lookup",
      how: "left",
      on: ["policy_id"],
    })

    fireEvent.click(screen.getByRole("radio", { name: "Paired base/join keys" }))

    expect(onUpdate).toHaveBeenCalledWith({
      on: [],
      leftOn: ["policy_id"],
      rightOn: ["policy_id"],
    })
  })

  it("updates paired key rows and can add another pair", () => {
    const { onUpdate } = renderEditor({
      baseInput: "quotes",
      joinInput: "lookup",
      how: "left",
      leftOn: ["policy_id"],
      rightOn: ["lookup_policy_id"],
    })

    expect(screen.getByRole("radio", { name: "Paired base/join keys" })).toHaveAttribute("aria-checked", "true")

    fireEvent.change(screen.getByLabelText("Join key 1"), { target: { value: "region" } })
    expect(onUpdate).toHaveBeenCalledWith({
      on: [],
      leftOn: ["policy_id"],
      rightOn: ["region"],
    })

    fireEvent.click(screen.getByRole("button", { name: "Add key pair" }))
    expect(onUpdate).toHaveBeenCalledWith({
      on: [],
      leftOn: ["policy_id", ""],
      rightOn: ["lookup_policy_id", ""],
    })
  })

  it("shows a clear diagnostic when same-name and paired keys are both configured", () => {
    renderEditor({
      baseInput: "quotes",
      joinInput: "lookup",
      how: "left",
      on: ["policy_id"],
      leftOn: ["state"],
      rightOn: ["region"],
    })

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Choose either same-name keys or paired base/join keys, not both.",
    )
  })

  it("shows a clear diagnostic when configured roles do not match connected handles", () => {
    renderEditor({
      baseInput: "missing_node",
      joinInput: "lookup",
      how: "left",
      on: ["policy_id"],
    })

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Base Input is set to missing_node, but the connected base handle is Quotes.",
    )
    expect(screen.queryByRole("combobox", { name: "Base Input" })).not.toBeInTheDocument()
    expect(screen.getAllByText("Quotes").length).toBeGreaterThan(0)
  })

  it("updates advanced Polars join options", () => {
    const { onUpdate } = renderEditor({
      baseInput: "quotes",
      joinInput: "lookup",
      how: "left",
      on: ["policy_id"],
    })

    fireEvent.change(screen.getByLabelText("Coalesce"), { target: { value: "true" } })
    expect(onUpdate).toHaveBeenCalledWith("coalesce", true)

    fireEvent.change(screen.getByLabelText("Validate"), { target: { value: "1:1" } })
    expect(onUpdate).toHaveBeenCalledWith("validate", "1:1")

    fireEvent.change(screen.getByLabelText("Maintain Order"), { target: { value: "left" } })
    expect(onUpdate).toHaveBeenCalledWith("maintainOrder", "left")
  })
})
