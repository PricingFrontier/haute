/**
 * The Workbench Output's panel (specs/workbench): the response's tables the project's
 * workbench supplies, each with the node connected to its port and the frame column that
 * fills each of its columns, and notes when nothing updates the tables.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import WorkbenchOutputEditor from "../../panels/editors/WorkbenchOutputEditor"
import { WORKBENCH_DISABLED_NOTE } from "../../panels/editors/WorkbenchInputEditor"
import type { InputSource } from "../../panels/editors/_shared"
import useWorkbenchStore from "../../stores/useWorkbenchStore"

const column = (name: string, type: string) => ({ name, type })
const config: Record<string, unknown> = {
  tables: [
    { name: "pricing_output", rows: "one", columns: [column("premium", "float"), column("referral", "str")] },
    { name: "layers", rows: "many", columns: [column("layer", "int"), column("premium", "float")] },
    { name: "two words", rows: "one", columns: [column("x", "int")] },
    { name: "bare", rows: "one", columns: [] },
  ],
}
const priced: InputSource = {
  sourceNodeId: "priced",
  name: "priced",
  sourceLabel: "priced",
  edgeId: "e_priced",
  targetHandle: "pricing_output",
  columns: [{ name: "premium", dtype: "Float64" }, { name: "base_premium", dtype: "Float64" }],
}

function renderEditor(
  props: { insideSubmodel?: boolean; inputSources?: InputSource[]; mapping?: Record<string, unknown> } = {},
) {
  const onUpdate = vi.fn(() => ({ ok: true as const }))
  render(
    <WorkbenchOutputEditor
      config={props.mapping === undefined ? config : { ...config, mapping: props.mapping }}
      onUpdate={onUpdate}
      inputSources={props.inputSources ?? [priced]}
      reservedFrameLabels={new Set(["class"])}
      insideSubmodel={props.insideSubmodel}
    />,
  )
  return { onUpdate }
}
const mapped = (table: string, name: string) =>
  screen.getByTestId(`workbench-mapping-${table}-${name}`) as HTMLSelectElement

describe("WorkbenchOutputEditor", () => {
  beforeEach(() => {
    useWorkbenchStore.setState({ enabled: true })
  })
  afterEach(() => {
    cleanup()
    useWorkbenchStore.setState({ enabled: false })
  })

  it("shows each table read-only with the node connected to its port", () => {
    renderEditor()

    const pricing = screen.getByTestId("workbench-table-pricing_output")
    expect(pricing).toHaveTextContent("one per quote")
    expect(within(pricing).getByText("referral")).toBeInTheDocument()
    expect(screen.getByTestId("workbench-table-connection-pricing_output")).toHaveTextContent("From priced")
    expect(screen.getByTestId("workbench-table-layers")).toHaveTextContent("many per quote")
    expect(screen.getByTestId("workbench-table-connection-layers")).toHaveTextContent("Not connected")
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    // A table without a column has no port, so nothing to connect.
    expect(screen.getByTestId("workbench-table-bare")).toHaveTextContent("No columns yet, so no port.")
    expect(screen.queryByTestId("workbench-table-connection-bare")).not.toBeInTheDocument()
  })

  it("shows what fills each column: a same-named column by name, or nothing, flagged", () => {
    renderEditor()

    expect(mapped("pricing_output", "premium").value).toBe("premium")
    expect(mapped("pricing_output", "premium")).toHaveTextContent("premium (by name)")
    expect(mapped("pricing_output", "referral").value).toBe("")
    expect(mapped("pricing_output", "referral")).toHaveAttribute("title", "Nothing fills this column: it is left empty")
    // A table with nothing connected has nothing to pick from.
    expect(mapped("layers", "premium")).toBeDisabled()
  })

  it("shows a pick, and flags one the connected frame lacks", () => {
    renderEditor({ mapping: { pricing_output: { premium: "base_premium", referral: "reason" } } })

    expect(mapped("pricing_output", "premium").value).toBe("base_premium")
    expect(mapped("pricing_output", "referral").value).toBe("reason")
    expect(mapped("pricing_output", "referral")).toHaveTextContent("reason (missing)")
  })

  it("sends a pick as the new mapping, keeps none as none, and drops a pick of the column's own name", () => {
    const { onUpdate } = renderEditor({ mapping: { pricing_output: { referral: null, premium: "base_premium" } } })

    fireEvent.change(mapped("pricing_output", "premium"), { target: { value: "base_premium" } })
    expect(onUpdate).toHaveBeenLastCalledWith("mapping", {
      pricing_output: { premium: "base_premium", referral: null },
    })

    fireEvent.change(mapped("pricing_output", "premium"), { target: { value: "" } })
    expect(onUpdate).toHaveBeenLastCalledWith("mapping", { pricing_output: { premium: null, referral: null } })

    // `referral` has no same-named column today, so none fills it either way; the pick is
    // kept as null all the same, so a `referral` the frame gains later does not fill it.
    fireEvent.change(mapped("pricing_output", "referral"), { target: { value: "" } })
    expect(onUpdate).toHaveBeenLastCalledWith("mapping", { pricing_output: { referral: null, premium: "base_premium" } })

    // The column's own name is filling by name: no entry.
    fireEvent.change(mapped("pricing_output", "premium"), { target: { value: "premium" } })
    expect(onUpdate).toHaveBeenLastCalledWith("mapping", { pricing_output: { referral: null } })
  })

  it("says what stops a table's name being a port", () => {
    renderEditor()

    expect(within(screen.getByTestId("workbench-table-two words")).getByRole("alert")).toBeInTheDocument()
    expect(within(screen.getByTestId("workbench-table-layers")).queryByRole("alert")).not.toBeInTheDocument()
  })

  it("says the tables are the last copy while the workbench is not enabled", () => {
    useWorkbenchStore.setState({ enabled: false })
    renderEditor()

    expect(screen.getByRole("note")).toHaveTextContent(WORKBENCH_DISABLED_NOTE)
    expect(within(screen.getByTestId("workbench-output-tables")).getByText("Tables")).toBeInTheDocument()
  })

  it("says the editor updates the tables only at the top level while a submodel is open", () => {
    renderEditor({ insideSubmodel: true })

    expect(screen.getByRole("note")).toHaveTextContent(
      "The editor updates these tables only at the pipeline's top level.",
    )
  })

  it("offers to edit the tables in the workbench's view while the workbench is enabled", () => {
    renderEditor()

    fireEvent.click(screen.getByRole("button", { name: "Edit in Workbench" }))
    expect(useWorkbenchStore.getState().activeView).toBe("workbench")
    useWorkbenchStore.setState({ activeView: "pipeline" })
  })
})
