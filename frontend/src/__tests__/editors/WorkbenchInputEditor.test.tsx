/**
 * The Workbench Input's panel (specs/workbench): the workbench's tables, read-only, with
 * each label's problem as a port, what previews run on, and notes when nothing updates
 * the tables.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import WorkbenchInputEditor, { WORKBENCH_DISABLED_NOTE } from "../../panels/editors/WorkbenchInputEditor"
import useWorkbenchStore from "../../stores/useWorkbenchStore"

const RESERVED = new Set(["class", "pl", "pipeline"])

const column = (name: string, type: string) => ({ name, type })
const config = {
  tables: [
    { name: "policy_details", rows: "one", columns: [column("state", "str"), column("inception_date", "date")] },
    { name: "equipment", rows: "many", columns: [column("item_id", "str"), column("value", "float")] },
    { name: "pl", rows: "one", columns: [column("x", "int")] },
    { name: "bare", rows: "one", columns: [] },
  ],
}
function renderEditor(props: { insideSubmodel?: boolean; sample?: Record<string, unknown> } = {}) {
  return render(
    <WorkbenchInputEditor
      config={props.sample === undefined ? config : { ...config, sample: props.sample }}
      accentColor="#E69F00"
      reservedFrameLabels={RESERVED}
      insideSubmodel={props.insideSubmodel}
    />,
  )
}

describe("WorkbenchInputEditor", () => {
  beforeEach(() => {
    useWorkbenchStore.setState({ enabled: true })
  })
  afterEach(() => {
    cleanup()
    useWorkbenchStore.setState({ enabled: false, activeView: "pipeline" })
  })

  it("shows the workbench's tables read-only, with nothing to edit them", () => {
    renderEditor()

    const tables = screen.getByTestId("workbench-input-tables")
    expect(within(tables).getByText("Tables from the workbench")).toBeInTheDocument()
    const policy = within(tables).getByTestId("workbench-table-policy_details")
    expect(within(policy).getByText("one per quote")).toBeInTheDocument()
    expect(within(policy).getByText("state")).toBeInTheDocument()
    expect(within(policy).getByText("date")).toBeInTheDocument()
    expect(within(within(tables).getByTestId("workbench-table-equipment")).getByText("many per quote")).toBeInTheDocument()
    // A table without a column has no frame, so no port.
    expect(within(tables).getByTestId("workbench-table-bare")).toHaveTextContent("No columns yet, so no port.")
    expect(screen.queryByText("Infer Tables")).not.toBeInTheDocument()
    expect(screen.queryAllByRole("textbox")).toEqual([])
    expect(screen.queryByRole("note")).not.toBeInTheDocument()

    // It reads no file: previews run on nulls, so there is no sample to choose.
    expect(screen.queryByText("Preview Data")).not.toBeInTheDocument()
    expect(screen.getByTestId("workbench-input-preview-note")).toHaveTextContent(
      "Previews run on one row of nulls per table until the workbench supplies values.",
    )
  })

  it("says previews run on the workbench's sample once it supplies one", () => {
    renderEditor({ sample: { policy_details: { state: "NY" } } })
    expect(screen.getByTestId("workbench-input-preview-note")).toHaveTextContent(
      "Previews run on the workbench's sample values; a table with none is one row of nulls.",
    )
    cleanup()

    renderEditor({ sample: {} })
    expect(screen.getByTestId("workbench-input-preview-note")).toHaveTextContent(
      "Previews run on one row of nulls per table until the workbench supplies values.",
    )
  })

  it("says what stops a table's name being a port", () => {
    renderEditor()

    const reserved = screen.getByTestId("workbench-table-pl")
    expect(within(reserved).getByRole("alert")).toHaveTextContent("cannot be a Python hard keyword or a name node code binds itself")
    expect(within(screen.getByTestId("workbench-table-policy_details")).queryByRole("alert")).not.toBeInTheDocument()
  })

  it("says the tables are the last copy while the workbench is not enabled", () => {
    useWorkbenchStore.setState({ enabled: false })
    renderEditor()

    expect(screen.getByRole("note")).toHaveTextContent(WORKBENCH_DISABLED_NOTE)
    expect(within(screen.getByTestId("workbench-input-tables")).getByText("Tables")).toBeInTheDocument()
    expect(screen.getByTestId("workbench-table-policy_details")).toBeInTheDocument()
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

    cleanup()
    useWorkbenchStore.setState({ enabled: false, activeView: "pipeline" })
    renderEditor()
    expect(screen.queryByRole("button", { name: "Edit in Workbench" })).not.toBeInTheDocument()
  })
})
